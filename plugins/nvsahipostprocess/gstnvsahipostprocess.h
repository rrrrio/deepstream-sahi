/*
 * File: gstnvsahipostprocess.h
 * Description: GstBaseTransform that performs in-place greedy NMM (Non-Maximum Merging)
 *              on NvDsObjectMeta, removing duplicate SAHI slice detections in C++.
 *
 *              Pipeline position: nvinfer → queue → nvsahipostprocess → nvtracker
 *
 * Author: Yuhao Ding
 */

#ifndef GST_NVSAHIPOSTPROCESS_H_
#define GST_NVSAHIPOSTPROCESS_H_

#include <gst/gst.h>
#include <gst/base/gstbasetransform.h>

G_BEGIN_DECLS

#define GST_TYPE_NVSAHIPOSTPROCESS            (gst_nvsahipostprocess_get_type())
#define GST_NVSAHIPOSTPROCESS(obj)            (G_TYPE_CHECK_INSTANCE_CAST((obj), GST_TYPE_NVSAHIPOSTPROCESS, GstNvSahiPostprocess))
#define GST_NVSAHIPOSTPROCESS_CLASS(klass)    (G_TYPE_CHECK_CLASS_CAST((klass), GST_TYPE_NVSAHIPOSTPROCESS, GstNvSahiPostprocessClass))
#define GST_IS_NVSAHIPOSTPROCESS(obj)         (G_TYPE_CHECK_INSTANCE_TYPE((obj), GST_TYPE_NVSAHIPOSTPROCESS))
#define GST_IS_NVSAHIPOSTPROCESS_CLASS(klass) (G_TYPE_CHECK_CLASS_TYPE((klass), GST_TYPE_NVSAHIPOSTPROCESS))

typedef enum {
    NV_SAHI_METRIC_IOS = 0,
    NV_SAHI_METRIC_IOU = 1,
} NvSahiMetric;

typedef struct _GstNvSahiPostprocess      GstNvSahiPostprocess;
typedef struct _GstNvSahiPostprocessClass GstNvSahiPostprocessClass;

struct _GstNvSahiPostprocess {
    GstBaseTransform parent;

    NvSahiMetric metric;
    gfloat       threshold;
    gboolean     class_agnostic;
};

struct _GstNvSahiPostprocessClass {
    GstBaseTransformClass parent_class;
};

GType gst_nvsahipostprocess_get_type(void);

#define GST_TYPE_NV_SAHI_METRIC (gst_nv_sahi_metric_get_type())
GType gst_nv_sahi_metric_get_type(void);

G_END_DECLS

#endif /* GST_NVSAHIPOSTPROCESS_H_ */
