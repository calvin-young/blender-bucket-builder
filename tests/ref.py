"""Slow, independent reference implementations used only by the tests."""

import math
import numpy as np


def _dot(a, b):
    return float(a[0] * b[0] + a[1] * b[1] + a[2] * b[2])


def closest_pt_on_tri(p, a, b, c):
    """Ericson, Real-Time Collision Detection 5.1.5 (region based)."""
    ab = b - a
    ac = c - a
    ap = p - a
    d1 = _dot(ab, ap)
    d2 = _dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        return a
    bp = p - b
    d3 = _dot(ab, bp)
    d4 = _dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return b
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3) if d1 != d3 else 0.0
        return a + v * ab
    cp = p - c
    d5 = _dot(ab, cp)
    d6 = _dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return c
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6) if d2 != d6 else 0.0
        return a + w * ac
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        den = (d4 - d3) + (d5 - d6)
        w = (d4 - d3) / den if den != 0 else 0.0
        return b + w * (c - b)
    den = va + vb + vc
    if den == 0:
        return a
    v = vb / den
    w = vc / den
    return a + ab * v + ac * w


def seg_seg_dist2(p1, q1, p2, q2):
    """Squared distance between two segments by exhaustive case analysis:
    the interior critical point of the quadratic plus its four boundary edges."""
    d1 = q1 - p1
    d2 = q2 - p2
    r = p1 - p2
    a = _dot(d1, d1)
    e = _dot(d2, d2)
    b = _dot(d1, d2)
    c = _dot(d1, r)
    f = _dot(d2, r)

    def val(s, t):
        v = r + s * d1 - t * d2
        return _dot(v, v)

    best = math.inf
    den = a * e - b * b
    if den > 1e-14 * max(a * e, 1e-300):
        s = (b * f - c * e) / den
        t = (a * f - b * c) / den
        if 0 <= s <= 1 and 0 <= t <= 1:
            best = min(best, val(s, t))
    for s in (0.0, 1.0):
        t = (b * s + f) / e if e > 0 else 0.0
        t = min(1.0, max(0.0, t))
        best = min(best, val(s, t))
    for t in (0.0, 1.0):
        s = (b * t - c) / a if a > 0 else 0.0
        s = min(1.0, max(0.0, s))
        best = min(best, val(s, t))
    return best


def seg_tri_hit(p, q, a, b, c, eps=1e-12):
    """Does segment pq pass through triangle abc (Moller-Trumbore)?"""
    d = q - p
    e1 = b - a
    e2 = c - a
    h = np.cross(d, e2)
    det = _dot(e1, h)
    if abs(det) < eps:
        return False
    inv = 1.0 / det
    s = p - a
    u = inv * _dot(s, h)
    if u < -1e-12 or u > 1 + 1e-12:
        return False
    qv = np.cross(s, e1)
    v = inv * _dot(d, qv)
    if v < -1e-12 or u + v > 1 + 1e-12:
        return False
    t = inv * _dot(e2, qv)
    return -1e-12 <= t <= 1 + 1e-12


def tri_tri_hit(P, Q):
    for i in range(3):
        if seg_tri_hit(P[i], P[(i + 1) % 3], Q[0], Q[1], Q[2]):
            return True
        if seg_tri_hit(Q[i], Q[(i + 1) % 3], P[0], P[1], P[2]):
            return True
    return False


def tri_tri_dist(P, Q):
    """Reference distance between two triangles (0 if they intersect)."""
    if tri_tri_hit(P, Q):
        return 0.0
    best = math.inf
    for i in range(3):
        cpt = closest_pt_on_tri(P[i], Q[0], Q[1], Q[2])
        v = P[i] - cpt
        best = min(best, _dot(v, v))
        cpt = closest_pt_on_tri(Q[i], P[0], P[1], P[2])
        v = Q[i] - cpt
        best = min(best, _dot(v, v))
        for j in range(3):
            best = min(best, seg_seg_dist2(P[i], P[(i + 1) % 3], Q[j], Q[(j + 1) % 3]))
    return math.sqrt(best)


def mesh_mesh_brute(VA, TA, VB, TB, chunk=2000):
    """Brute-force minimum distance and intersecting-pair list for two small
    meshes, using the reference routines above for every triangle pair whose
    bounding boxes are close enough to matter."""
    PA = VA[TA]
    PB = VB[TB]
    best = math.inf
    hits = []
    loA = PA.min(1)
    hiA = PA.max(1)
    loB = PB.min(1)
    hiB = PB.max(1)
    # cheap vectorised lower bound to skip far pairs
    for i in range(len(PA)):
        gap = np.maximum(np.maximum(loA[i] - hiB, loB - hiA[i]), 0.0)
        lb = np.sqrt((gap * gap).sum(1))
        cand = np.flatnonzero(lb <= best + 1e-12)
        order = cand[np.argsort(lb[cand])]
        for j in order:
            if lb[j] > best:
                break
            if lb[j] == 0.0 and tri_tri_hit(PA[i], PB[j]):
                hits.append((i, int(j)))
                best = 0.0
                continue
            d = tri_tri_dist(PA[i], PB[j])
            if d < best:
                best = d
    return best, hits
