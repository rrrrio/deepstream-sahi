/*
 * File: gstnvsahipreprocess.h
 * Description: GstBin that wraps nvdspreprocess with dynamic SAHI ROI computation.
 *              Eliminates the need for a static config file by computing ROIs
 *              from slice parameters and frame dimensions at runtime.
 * Author: Yuhao Ding
 */

#ifndef GST_NVSAHIPREPROCESS_H_
#define GST_NVSAHIPREPROCESS_H_

#include <gst/gst.h>

G_BEGIN_DECLS

#define GST_TYPE_NVSAHIPREPROCESS            (gst_nvsahipreprocess_get_type())
#define GST_NVSAHIPREPROCESS(obj)            (G_TYPE_CHECK_INSTANCE_CAST((obj), GST_TYPE_NVSAHIPREPROCESS, GstNvSahiPreprocess))
#define GST_NVSAHIPREPROCESS_CLASS(klass)    (G_TYPE_CHECK_CLASS_CAST((klass), GST_TYPE_NVSAHIPREPROCESS, GstNvSahiPreprocessClass))
#define GST_IS_NVSAHIPREPROCESS(obj)         (G_TYPE_CHECK_INSTANCE_TYPE((obj), GST_TYPE_NVSAHIPREPROCESS))
#define GST_IS_NVSAHIPREPROCESS_CLASS(klass) (G_TYPE_CHECK_CLASS_TYPE((klass), GST_TYPE_NVSAHIPREPROCESS))

typedef struct _GstNvSahiPreprocess      GstNvSahiPreprocess;
typedef struct _GstNvSahiPreprocessClass GstNvSahiPreprocessClass;

struct _GstNvSahiPreprocess {
    GstBin parent;

    GstElement *preprocess;
    GstPad     *sinkpad;
    GstPad     *srcpad;

    /* SAHI slice parameters */
    gint   slice_width;
    gint   slice_height;
    gfloat overlap_h;
    gfloat overlap_v;

    /* Frame dimensions (set by user or auto-detected from caps) */
    gint   frame_width;
    gint   frame_height;

    /* nvdspreprocess passthrough config */
    gchar *custom_lib_path;
    gchar *tensor_name;
    gfloat pixel_norm_factor;
    gint   gpu_id;
    gint   scaling_buf_pool_size;
    gint   tensor_buf_pool_size;
    gint   network_color_format;
    gint   tensor_data_type;

    /* Internal state */
    gchar   *config_path;
    gboolean config_generated;
};

struct _GstNvSahiPreprocessClass {
    GstBinClass parent_class;
};

GType gst_nvsahipreprocess_get_type(void);

G_END_DECLS

#endif /* GST_NVSAHIPREPROCESS_H_ */
