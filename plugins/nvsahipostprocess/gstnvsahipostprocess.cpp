/*
 * File: gstnvsahipostprocess.cpp
 * Description: C++ GstBaseTransform performing greedy NMM on NvDsObjectMeta.
 *              Operates in-place on each buffer, removing duplicate detections
 *              from overlapping SAHI slices before they reach the tracker.
 *
 *              Significantly faster than the Python probe equivalent because
 *              it iterates NvDsMeta in native C++ without GIL overhead.
 *
 *              Properties:
 *                metric    — overlap metric: ios (intersection-over-smaller) or iou
 *                threshold — merge threshold [0, 1]
 *                class-agnostic — merge across classes
 *
 * Author: Yuhao Ding
 */

#include "gstnvsahipostprocess.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <tuple>
#include <vector>

#include "gstnvdsmeta.h"
#include "nvdsmeta.h"

GST_DEBUG_CATEGORY_STATIC(gst_nvsahipostprocess_debug);
#define GST_CAT_DEFAULT gst_nvsahipostprocess_debug

/* ─── property defaults ──────────────────────────────────────────────── */

enum {
    PROP_0,
    PROP_METRIC,
    PROP_THRESHOLD,
    PROP_CLASS_AGNOSTIC,
};

#define DEFAULT_METRIC    NV_SAHI_METRIC_IOS
#define DEFAULT_THRESHOLD 0.5f
#define DEFAULT_CLASS_AGN FALSE

/* ─── metric enum GType ──────────────────────────────────────────────── */

GType
gst_nv_sahi_metric_get_type(void)
{
    static GType type = 0;
    if (g_once_init_enter(&type)) {
        static const GEnumValue values[] = {
            {NV_SAHI_METRIC_IOS, "Intersection over Smaller", "ios"},
            {NV_SAHI_METRIC_IOU, "Intersection over Union",   "iou"},
            {0, NULL, NULL}
        };
        GType t = g_enum_register_static("NvSahiMetric", values);
        g_once_init_leave(&type, t);
    }
    return type;
}

/* ─── pad templates ──────────────────────────────────────────────────── */

static GstStaticPadTemplate sink_template =
    GST_STATIC_PAD_TEMPLATE("sink", GST_PAD_SINK, GST_PAD_ALWAYS, GST_STATIC_CAPS_ANY);

static GstStaticPadTemplate src_template =
    GST_STATIC_PAD_TEMPLATE("src", GST_PAD_SRC, GST_PAD_ALWAYS, GST_STATIC_CAPS_ANY);

#define gst_nvsahipostprocess_parent_class parent_class
G_DEFINE_TYPE(GstNvSahiPostprocess, gst_nvsahipostprocess, GST_TYPE_BASE_TRANSFORM);

/* ─── NMM core ───────────────────────────────────────────────────────── */

struct Detection {
    NvDsObjectMeta *obj_meta;
    float x1, y1, x2, y2;
    float confidence;
    int   class_id;
};

static inline float
box_area(float x1, float y1, float x2, float y2)
{
    float w = std::max(0.0f, x2 - x1);
    float h = std::max(0.0f, y2 - y1);
    return w * h;
}

static inline float
compute_overlap(const Detection &a, const Detection &b, NvSahiMetric metric)
{
    float ix1 = std::max(a.x1, b.x1);
    float iy1 = std::max(a.y1, b.y1);
    float ix2 = std::min(a.x2, b.x2);
    float iy2 = std::min(a.y2, b.y2);

    if (ix2 <= ix1 || iy2 <= iy1)
        return 0.0f;

    float inter = (ix2 - ix1) * (iy2 - iy1);
    float area_a = box_area(a.x1, a.y1, a.x2, a.y2);
    float area_b = box_area(b.x1, b.y1, b.x2, b.y2);

    if (metric == NV_SAHI_METRIC_IOU) {
        float u = area_a + area_b - inter;
        return (u > 0.0f) ? inter / u : 0.0f;
    }
    float smaller = std::min(area_a, area_b);
    return (smaller > 0.0f) ? inter / smaller : 0.0f;
}

static void
run_nmm(NvDsFrameMeta *frame_meta, NvSahiMetric metric, float threshold, gboolean class_agnostic)
{
    std::vector<Detection> dets;

    NvDsObjectMetaList *l_obj = frame_meta->obj_meta_list;
    while (l_obj) {
        NvDsObjectMeta *obj = (NvDsObjectMeta *)l_obj->data;
        if (!obj) { l_obj = l_obj->next; continue; }

        Detection d;
        d.obj_meta   = obj;
        d.x1         = obj->rect_params.left;
        d.y1         = obj->rect_params.top;
        d.x2         = d.x1 + obj->rect_params.width;
        d.y2         = d.y1 + obj->rect_params.height;
        d.confidence = obj->confidence;
        d.class_id   = (int)obj->class_id;
        dets.push_back(d);

        l_obj = l_obj->next;
    }

    if (dets.size() <= 1)
        return;

    /* Sort by confidence desc, then bbox for determinism */
    std::sort(dets.begin(), dets.end(), [](const Detection &a, const Detection &b) {
        if (a.confidence != b.confidence) return a.confidence > b.confidence;
        return std::tie(a.x1, a.y1, a.x2, a.y2) < std::tie(b.x1, b.y1, b.x2, b.y2);
    });

    int n = (int)dets.size();
    std::vector<bool> suppressed(n, false);

    /* keep_bbox[i] tracks the evolving merged bbox for survivor i */
    std::vector<std::array<float, 4>> keep_bbox(n);
    for (int i = 0; i < n; i++)
        keep_bbox[i] = {dets[i].x1, dets[i].y1, dets[i].x2, dets[i].y2};

    float best_conf[n];
    int   best_class[n];
    for (int i = 0; i < n; i++) {
        best_conf[i]  = dets[i].confidence;
        best_class[i] = dets[i].class_id;
    }

    for (int i = 0; i < n; i++) {
        if (suppressed[i]) continue;

        for (int j = i + 1; j < n; j++) {
            if (suppressed[j]) continue;
            if (!class_agnostic && dets[j].class_id != dets[i].class_id)
                continue;

            Detection probe_i = dets[i];
            probe_i.x1 = keep_bbox[i][0]; probe_i.y1 = keep_bbox[i][1];
            probe_i.x2 = keep_bbox[i][2]; probe_i.y2 = keep_bbox[i][3];

            if (compute_overlap(probe_i, dets[j], metric) >= threshold) {
                /* Merge j into i */
                keep_bbox[i][0] = std::min(keep_bbox[i][0], dets[j].x1);
                keep_bbox[i][1] = std::min(keep_bbox[i][1], dets[j].y1);
                keep_bbox[i][2] = std::max(keep_bbox[i][2], dets[j].x2);
                keep_bbox[i][3] = std::max(keep_bbox[i][3], dets[j].y2);

                if (dets[j].confidence > best_conf[i]) {
                    best_conf[i]  = dets[j].confidence;
                    best_class[i] = dets[j].class_id;
                }
                suppressed[j] = true;
            }
        }
    }

    /* Apply: update survivors, remove suppressed */
    for (int i = 0; i < n; i++) {
        if (suppressed[i]) continue;

        NvDsObjectMeta *obj = dets[i].obj_meta;
        float x1 = keep_bbox[i][0], y1 = keep_bbox[i][1];
        float x2 = keep_bbox[i][2], y2 = keep_bbox[i][3];
        float w = std::max(0.0f, x2 - x1);
        float h = std::max(0.0f, y2 - y1);

        obj->rect_params.left   = x1;
        obj->rect_params.top    = y1;
        obj->rect_params.width  = w;
        obj->rect_params.height = h;
        obj->class_id           = (guint)best_class[i];
        obj->confidence         = best_conf[i];

        obj->detector_bbox_info.org_bbox_coords.left   = x1;
        obj->detector_bbox_info.org_bbox_coords.top    = y1;
        obj->detector_bbox_info.org_bbox_coords.width  = w;
        obj->detector_bbox_info.org_bbox_coords.height = h;
    }

    for (int i = n - 1; i >= 0; i--) {
        if (suppressed[i]) {
            nvds_remove_obj_meta_from_frame(frame_meta, dets[i].obj_meta);
        }
    }
}

/* ─── transform_ip ───────────────────────────────────────────────────── */

static GstFlowReturn
gst_nvsahipostprocess_transform_ip(GstBaseTransform *base, GstBuffer *buf)
{
    GstNvSahiPostprocess *self = GST_NVSAHIPOSTPROCESS(base);

    NvDsBatchMeta *batch_meta = gst_buffer_get_nvds_batch_meta(buf);
    if (!batch_meta)
        return GST_FLOW_OK;

    nvds_acquire_meta_lock(batch_meta);

    NvDsFrameMetaList *l_frame = batch_meta->frame_meta_list;
    while (l_frame) {
        NvDsFrameMeta *frame_meta = (NvDsFrameMeta *)l_frame->data;
        if (frame_meta)
            run_nmm(frame_meta, self->metric, self->threshold, self->class_agnostic);
        l_frame = l_frame->next;
    }

    nvds_release_meta_lock(batch_meta);
    return GST_FLOW_OK;
}

/* ─── property accessors ─────────────────────────────────────────────── */

static void
gst_nvsahipostprocess_set_property(GObject *object, guint prop_id,
                                    const GValue *value, GParamSpec *pspec)
{
    GstNvSahiPostprocess *self = GST_NVSAHIPOSTPROCESS(object);
    switch (prop_id) {
    case PROP_METRIC:        self->metric        = (NvSahiMetric)g_value_get_enum(value); break;
    case PROP_THRESHOLD:     self->threshold      = g_value_get_float(value); break;
    case PROP_CLASS_AGNOSTIC: self->class_agnostic = g_value_get_boolean(value); break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec); break;
    }
}

static void
gst_nvsahipostprocess_get_property(GObject *object, guint prop_id,
                                    GValue *value, GParamSpec *pspec)
{
    GstNvSahiPostprocess *self = GST_NVSAHIPOSTPROCESS(object);
    switch (prop_id) {
    case PROP_METRIC:        g_value_set_enum(value, self->metric); break;
    case PROP_THRESHOLD:     g_value_set_float(value, self->threshold); break;
    case PROP_CLASS_AGNOSTIC: g_value_set_boolean(value, self->class_agnostic); break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec); break;
    }
}

/* ─── init / class_init ──────────────────────────────────────────────── */

static void
gst_nvsahipostprocess_init(GstNvSahiPostprocess *self)
{
    self->metric        = DEFAULT_METRIC;
    self->threshold     = DEFAULT_THRESHOLD;
    self->class_agnostic = DEFAULT_CLASS_AGN;

    gst_base_transform_set_in_place(GST_BASE_TRANSFORM(self), TRUE);
    gst_base_transform_set_passthrough(GST_BASE_TRANSFORM(self), TRUE);
}

static void
gst_nvsahipostprocess_class_init(GstNvSahiPostprocessClass *klass)
{
    GObjectClass          *gobject_class = G_OBJECT_CLASS(klass);
    GstElementClass       *element_class = GST_ELEMENT_CLASS(klass);
    GstBaseTransformClass *bt_class      = GST_BASE_TRANSFORM_CLASS(klass);

    gobject_class->set_property = gst_nvsahipostprocess_set_property;
    gobject_class->get_property = gst_nvsahipostprocess_get_property;

    bt_class->transform_ip = gst_nvsahipostprocess_transform_ip;

    g_object_class_install_property(gobject_class, PROP_METRIC,
        g_param_spec_enum("metric", "Overlap Metric",
            "Overlap metric for NMM: ios (intersection-over-smaller) or iou",
            GST_TYPE_NV_SAHI_METRIC, DEFAULT_METRIC,
            (GParamFlags)(G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS)));

    g_object_class_install_property(gobject_class, PROP_THRESHOLD,
        g_param_spec_float("threshold", "Merge Threshold",
            "Overlap threshold above which detections are merged",
            0.0f, 1.0f, DEFAULT_THRESHOLD,
            (GParamFlags)(G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS)));

    g_object_class_install_property(gobject_class, PROP_CLASS_AGNOSTIC,
        g_param_spec_boolean("class-agnostic", "Class Agnostic",
            "If TRUE, merge detections across different classes",
            DEFAULT_CLASS_AGN,
            (GParamFlags)(G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS)));

    gst_element_class_set_static_metadata(element_class,
        "NV SAHI Postprocess",
        "Filter/Postprocessor/Video",
        "In-place greedy Non-Maximum Merging (NMM) on NvDsObjectMeta — "
        "removes duplicate SAHI detections in native C++ before the tracker",
        "Yuhao Ding <d346863528@163.com>");

    gst_element_class_add_static_pad_template(element_class, &sink_template);
    gst_element_class_add_static_pad_template(element_class, &src_template);

    GST_DEBUG_CATEGORY_INIT(gst_nvsahipostprocess_debug,
        "nvsahipostprocess", 0, "NV SAHI NMM postprocess");
}

/* ─── plugin entry ───────────────────────────────────────────────────── */

static gboolean
plugin_init(GstPlugin *plugin)
{
    return gst_element_register(plugin, "nvsahipostprocess",
                                GST_RANK_NONE, GST_TYPE_NVSAHIPOSTPROCESS);
}

GST_PLUGIN_DEFINE(
    GST_VERSION_MAJOR,
    GST_VERSION_MINOR,
    nvsahipostprocess,
    "In-place NMM postprocessor for SAHI DeepStream pipelines",
    plugin_init,
    "1.0",
    "MIT",
    "deepstream-sahi",
    "https://github.com/hasantahir/deepstream-sahi"
)
