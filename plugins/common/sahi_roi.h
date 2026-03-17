/*
 * File: sahi_roi.h
 * Description: SAHI ROI computation utilities shared between plugins.
 * Author: Yuhao Ding
 */

#ifndef SAHI_ROI_H_
#define SAHI_ROI_H_

#include <stdlib.h>
#include <string.h>

typedef struct {
    int left;
    int top;
    int width;
    int height;
} SahiRoi;

typedef struct {
    SahiRoi *rois;
    int count;
} SahiRoiList;

static inline SahiRoiList
sahi_compute_rois(int frame_w, int frame_h,
                  int slice_w, int slice_h,
                  float overlap_h, float overlap_v)
{
    int stride_x = slice_w - (int)(slice_w * overlap_h);
    int stride_y = slice_h - (int)(slice_h * overlap_v);
    if (stride_x < 1) stride_x = 1;
    if (stride_y < 1) stride_y = 1;

    int max_cols = (frame_w + stride_x - 1) / stride_x + 1;
    int max_rows = (frame_h + stride_y - 1) / stride_y + 1;
    int capacity = max_cols * max_rows + 1;

    SahiRoi *rois = (SahiRoi *)calloc(capacity, sizeof(SahiRoi));
    if (!rois) {
        SahiRoiList empty = {NULL, 0};
        return empty;
    }

    int xs[256], nx = 0;
    for (int x = 0; x + slice_w <= frame_w && nx < 255; x += stride_x)
        xs[nx++] = x;
    int edge_x = frame_w - slice_w;
    if (edge_x >= 0 && (nx == 0 || xs[nx - 1] != edge_x))
        xs[nx++] = edge_x;

    int ys[256], ny = 0;
    for (int y = 0; y + slice_h <= frame_h && ny < 255; y += stride_y)
        ys[ny++] = y;
    int edge_y = frame_h - slice_h;
    if (edge_y >= 0 && (ny == 0 || ys[ny - 1] != edge_y))
        ys[ny++] = edge_y;

    int n = 0;
    for (int j = 0; j < ny; j++) {
        for (int i = 0; i < nx; i++) {
            rois[n].left   = xs[i];
            rois[n].top    = ys[j];
            rois[n].width  = slice_w;
            rois[n].height = slice_h;
            n++;
        }
    }

    /* Full-frame ROI */
    rois[n].left   = 0;
    rois[n].top    = 0;
    rois[n].width  = frame_w;
    rois[n].height = frame_h;
    n++;

    SahiRoiList list = {rois, n};
    return list;
}

static inline void
sahi_roi_list_free(SahiRoiList *list)
{
    if (list && list->rois) {
        free(list->rois);
        list->rois = NULL;
        list->count = 0;
    }
}

#endif /* SAHI_ROI_H_ */
