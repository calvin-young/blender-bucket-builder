/* SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Compiled versions of the packer's hottest voxel steps (pack/voxel.py).
 * Each gives exactly the cells its NumPy original gives: the same
 * subdivision, the same rounding at cell boundaries, the same flood fill.
 * Loaded with ctypes (pack/native/__init__.py), so one library per platform
 * serves every Blender and Python version.  Build: tools/build_native.sh.
 * Compile without fused multiply-add (-ffp-contract=off): NumPy doesn't fuse.
 */
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API __attribute__((visibility("default")))
#endif

#define EDGE 1e-7

API int bb_version(void) { return 2; }

/* ------------------------------------------------------------ subdivision */

typedef struct { double *p; int64_t n, cap; } Buf;

static int push(Buf *b, const double *t) {
    if (b->n == b->cap) {
        int64_t cap = b->cap ? b->cap * 2 : 4096;
        double *q = realloc(b->p, (size_t)cap * 9 * sizeof(double));
        if (!q) return 0;
        b->p = q; b->cap = cap;
    }
    memcpy(b->p + b->n * 9, t, 9 * sizeof(double));
    b->n++;
    return 1;
}

static double d2(const double *a, const double *b) {
    double x = b[0] - a[0], y = b[1] - a[1], z = b[2] - a[2];
    return x * x + y * y + z * z;
}

/* Triangles (n, 3, 3) cut in two across their longest edge (the first, on a
 * tie) until no edge is longer than h.  Returns the number of pieces and sets
 * *out (free it with bb_free); -1 when memory runs out or there would be more
 * than max_pieces. */
API int64_t bb_subdivide(const double *P, int64_t n, double h, int64_t max_pieces, double **out) {
    double h2 = h * h * (1.0 - 1e-12);
    Buf res = {0}, st = {0};
    *out = NULL;
    for (int64_t i = 0; i < n; i++) {
        if (!push(&st, P + i * 9)) goto fail;
        while (st.n) {
            double t[9];
            memcpy(t, st.p + (st.n - 1) * 9, sizeof t);
            st.n--;
            double e[3] = {d2(t, t + 3), d2(t + 3, t + 6), d2(t + 6, t)};
            int k = 0;
            if (e[1] > e[k]) k = 1;
            if (e[2] > e[k]) k = 2;
            if (!(e[k] > h2)) {
                if (!push(&res, t)) goto fail;
                if (max_pieces > 0 && res.n > max_pieces) goto fail;
                continue;
            }
            const double *a = t + 3 * k, *b = t + 3 * ((k + 1) % 3), *c = t + 3 * ((k + 2) % 3);
            double m[3] = {0.5 * (a[0] + b[0]), 0.5 * (a[1] + b[1]), 0.5 * (a[2] + b[2])};
            double t1[9], t2[9];
            memcpy(t1, a, 24); memcpy(t1 + 3, m, 24); memcpy(t1 + 6, c, 24);
            memcpy(t2, m, 24); memcpy(t2 + 3, b, 24); memcpy(t2 + 6, c, 24);
            if (!push(&st, t2) || !push(&st, t1)) goto fail;
        }
    }
    free(st.p);
    *out = res.p;
    return res.n;
fail:
    free(st.p);
    free(res.p);
    return -1;
}

API void bb_free(void *p) { free(p); }

/* ------------------------------------------------------------ marking */

static void mark_one(uint8_t *grid, const int64_t *dims, const double *pmin, const double *pmax,
                     const double *nrm, const double *origin, double h) {
    int64_t lo[3], hi[3];
    for (int ax = 0; ax < 3; ax++) {
        double fmin = (pmin[ax] - origin[ax]) / h;
        double fmax = (pmax[ax] - origin[ax]) / h;
        if (!nrm) {
            lo[ax] = (int64_t)floor(fmin);
            hi[ax] = (int64_t)floor(fmax);
        } else {
            lo[ax] = (int64_t)floor(fmin + EDGE);
            int64_t hh = (int64_t)floor(fmax - EDGE);
            hi[ax] = hh > lo[ax] ? hh : lo[ax];
            double r = nearbyint(fmin);           /* (half to even, as np.rint) */
            if ((fmax - fmin < EDGE) && (fabs(fmin - r) < EDGE)) {
                if (nrm[ax] > 0) lo[ax] = hi[ax] = (int64_t)r - 1;
                else lo[ax] = hi[ax] = (int64_t)r;
            }
        }
        if (lo[ax] < 0) lo[ax] = 0;
        if (lo[ax] > dims[ax] - 1) lo[ax] = dims[ax] - 1;
        if (hi[ax] < 0) hi[ax] = 0;
        if (hi[ax] > dims[ax] - 1) hi[ax] = dims[ax] - 1;
    }
    /* a piece no wider than h touches at most two cells per axis; the
       NumPy version marks min(lo + d, hi) for d in 0, 1 */
    for (int dx = 0; dx < 2; dx++) {
        int64_t ix = lo[0] + dx < hi[0] ? lo[0] + dx : hi[0];
        for (int dy = 0; dy < 2; dy++) {
            int64_t iy = lo[1] + dy < hi[1] ? lo[1] + dy : hi[1];
            for (int dz = 0; dz < 2; dz++) {
                int64_t iz = lo[2] + dz < hi[2] ? lo[2] + dz : hi[2];
                grid[(ix * dims[1] + iy) * dims[2] + iz] = 1;
            }
        }
    }
}

/* _mark_boxes: boxes (n, 3) min and max, optional normals (n, 3). */
API void bb_mark_boxes(uint8_t *grid, const int64_t *dims, const double *pmin, const double *pmax,
                       const double *normals, int64_t n, const double *origin, double h) {
    for (int64_t i = 0; i < n; i++)
        mark_one(grid, dims, pmin + 3 * i, pmax + 3 * i, normals ? normals + 3 * i : NULL, origin, h);
}

/* _mark for triangles (n, 3, 3): their boxes and normals, then marked. */
API void bb_mark_tris(uint8_t *grid, const int64_t *dims, const double *P, int64_t n,
                      const double *origin, double h) {
    for (int64_t i = 0; i < n; i++) {
        const double *a = P + 9 * i, *b = a + 3, *c = a + 6;
        double mn[3], mx[3], u[3], v[3], nr[3];
        for (int ax = 0; ax < 3; ax++) {
            double lo = a[ax] < b[ax] ? a[ax] : b[ax];
            mn[ax] = lo < c[ax] ? lo : c[ax];
            double hi = a[ax] > b[ax] ? a[ax] : b[ax];
            mx[ax] = hi > c[ax] ? hi : c[ax];
            u[ax] = b[ax] - a[ax];
            v[ax] = c[ax] - a[ax];
        }
        nr[0] = u[1] * v[2] - u[2] * v[1];
        nr[1] = u[2] * v[0] - u[0] * v[2];
        nr[2] = u[0] * v[1] - u[1] * v[0];
        mark_one(grid, dims, mn, mx, nr, origin, h);
    }
}

/* _mark_boxes for a quarter turn of the pieces' frame: axis k of the result
   is axis axes[k] of the boxes, flipped when signs[k] < 0 (a box's min and
   max swap and change sign; so does its normal). */
API void bb_mark_boxes_turned(uint8_t *grid, const int64_t *dims, const double *pmin, const double *pmax,
                              const double *normals, int64_t n, const int64_t *axes, const double *signs,
                              const double *origin, double h) {
    for (int64_t i = 0; i < n; i++) {
        double mn[3], mx[3], nr[3];
        for (int k = 0; k < 3; k++) {
            double a = pmin[3 * i + axes[k]], b = pmax[3 * i + axes[k]];
            if (signs[k] < 0) { mn[k] = -b; mx[k] = -a; }
            else { mn[k] = a; mx[k] = b; }
            nr[k] = normals[3 * i + axes[k]] * signs[k];
        }
        mark_one(grid, dims, mn, mx, nr, origin, h);
    }
}

/* ------------------------------------------------------------ flood fill */

/* out = empty cells 6-connected to the grid's border through empty cells
   (blocked: 1 = solid).  A span fill: runs along z are filled whole, and
   only one seed per run of the neighbouring columns is pushed.  Returns 0,
   or -1 when memory runs out. */
API int bb_outside(const uint8_t *blocked, uint8_t *out, const int64_t *dims) {
    int64_t nx = dims[0], ny = dims[1], nz = dims[2];
    int64_t n = nx * ny * nz, top = 0, cap = 1 << 16;
    int64_t sx = ny * nz, sy = nz;
    memset(out, 0, (size_t)n);
    if (n == 0) return 0;
    int64_t *st = malloc(sizeof(int64_t) * (size_t)cap);
    if (!st) return -1;
#define FREE(i) (!blocked[i] && !out[i])
#define PUSH(i) do { if (top == cap) { int64_t *q = realloc(st, sizeof(int64_t) * (size_t)(cap * 2)); \
        if (!q) { free(st); return -1; } st = q; cap *= 2; } st[top++] = (i); } while (0)
    /* seeds: every free cell on the border (pushing all is simple; the span
       step skips cells already filled) */
    for (int64_t x = 0; x < nx; x++)
        for (int64_t y = 0; y < ny; y++) {
            int64_t col = x * sx + y * sy;
            if (x == 0 || x == nx - 1 || y == 0 || y == ny - 1) {
                for (int64_t z = 0; z < nz; z++) if (FREE(col + z)) PUSH(col + z);
            } else {
                if (FREE(col)) PUSH(col);
                if (nz > 1 && FREE(col + nz - 1)) PUSH(col + nz - 1);
            }
        }
    while (top) {
        int64_t i = st[--top];
        if (!FREE(i)) continue;
        int64_t z = i % nz, col = i - z;
        int64_t z0 = z, z1 = z;
        while (z0 > 0 && FREE(col + z0 - 1)) z0--;
        while (z1 < nz - 1 && FREE(col + z1 + 1)) z1++;
        memset(out + col + z0, 1, (size_t)(z1 - z0 + 1));
        int64_t r = col / nz, y = r % ny, x = r / ny;
        int64_t nb[4]; int nn = 0;
        if (x > 0) nb[nn++] = col - sx;
        if (x < nx - 1) nb[nn++] = col + sx;
        if (y > 0) nb[nn++] = col - sy;
        if (y < ny - 1) nb[nn++] = col + sy;
        for (int k = 0; k < nn; k++) {
            int in_run = 0;
            for (int64_t zz = z0; zz <= z1; zz++) {
                int f = FREE(nb[k] + zz);
                if (f && !in_run) PUSH(nb[k] + zz);
                in_run = f;
            }
        }
    }
#undef PUSH
#undef FREE
    free(st);
    return 0;
}
