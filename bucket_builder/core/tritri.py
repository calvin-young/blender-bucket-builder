# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact triangle/triangle tests, vectorised with NumPy over K triangle pairs.

Layout: every array keeps the pair index on the LAST axis and the coordinate
on the FIRST, so a stack of K triangles is ``(3 coords, 3 corners, K)``.  With
that layout each arithmetic step is one contiguous pass over K values (SIMD
friendly); the more obvious ``(K, 3, 3)`` layout makes NumPy loop over tiny
inner axes and is roughly ten times slower.

There is no Python-level loop over triangles anywhere.
"""

import numpy as np

_TINY = 1e-300
_NEXT = [1, 2, 0]


def _dot(a, b):
    """Dot product over the first (coordinate) axis."""
    out = a[0] * b[0]
    out += a[1] * b[1]
    out += a[2] * b[2]
    return out


def _cross(a, b):
    """Cross product over the first (coordinate) axis."""
    out = np.empty(np.broadcast_shapes(a.shape, b.shape), dtype=np.float64)
    np.multiply(a[1], b[2], out=out[0])
    out[0] -= a[2] * b[1]
    np.multiply(a[2], b[0], out=out[1])
    out[1] -= a[0] * b[2]
    np.multiply(a[0], b[1], out=out[2])
    out[2] -= a[1] * b[0]
    return out


def to_soa(tris):
    """(K, 3 corners, 3 coords) of any float type -> float64 (3, 3, K)."""
    return np.ascontiguousarray(tris.transpose(2, 1, 0), dtype=np.float64)


def _pick(arr, sel):
    """arr (3 coords, 3 corners, k), sel (k,) corner index -> (3, k)."""
    return np.take_along_axis(arr, sel[None, None, :], axis=1)[:, 0, :]


def _cut(tri, dist, odd):
    """Points where the two edges leaving corner ``odd`` cross a plane.

    ``dist`` (3 corners, k) holds the signed plane distances of the corners;
    ``odd`` is the corner that sits alone on its side of the plane.
    """
    a = (odd + 1) % 3
    b = (odd + 2) % 3
    t_o = _pick(tri, odd)
    d_o = np.take_along_axis(dist, odd[None, :], axis=0)[0]
    d_a = np.take_along_axis(dist, a[None, :], axis=0)[0]
    d_b = np.take_along_axis(dist, b[None, :], axis=0)[0]
    x1 = t_o + (d_o / (d_o - d_a)) * (_pick(tri, a) - t_o)
    x2 = t_o + (d_o / (d_o - d_b)) * (_pick(tri, b) - t_o)
    return x1, x2


def tri_tri_intersect(P, Q, eps):
    """Intersection segments of triangle pairs.

    Parameters
    ----------
    P, Q : (3, 3, K) float64, see ``to_soa``
    eps : float
        Corners closer than this to the other triangle's plane are treated as
        lying just on its positive side.  Pairs that merely touch, or that are
        coplanar, therefore report no segment here; ``tri_tri_distance`` then
        returns 0 for them, so they are still caught as contact.

    Returns
    -------
    hit : (K,) bool
    seg : (n_hit, 2, 3) float64 -- segment end points of the hits, in order.
    """
    K = P.shape[2]
    hit = np.zeros(K, dtype=bool)
    if K == 0:
        return hit, np.zeros((0, 2, 3))

    p0 = P[:, 0]
    q0 = Q[:, 0]
    nP = _cross(P[:, 1] - p0, P[:, 2] - p0)
    nQ = _cross(Q[:, 1] - q0, Q[:, 2] - q0)
    lP = np.sqrt(_dot(nP, nP))
    lQ = np.sqrt(_dot(nQ, nQ))
    good = (lP > 0.0) & (lQ > 0.0)
    nP /= np.maximum(lP, _TINY)
    nQ /= np.maximum(lQ, _TINY)

    dP = _dot(P - q0[:, None, :], nQ[:, None, :])       # (3 corners, K)
    dQ = _dot(Q - p0[:, None, :], nP[:, None, :])
    dP[np.abs(dP) < eps] = eps
    dQ[np.abs(dQ) < eps] = eps
    sP = dP > 0.0
    sQ = dQ > 0.0
    cP = sP.sum(axis=0)
    cQ = sQ.sum(axis=0)
    cand = good & (cP > 0) & (cP < 3) & (cQ > 0) & (cQ < 3)
    idx = np.flatnonzero(cand)
    if idx.shape[0] == 0:
        return hit, np.zeros((0, 2, 3))

    Ps = np.take(P, idx, axis=2)
    Qs = np.take(Q, idx, axis=2)
    dPs = np.take(dP, idx, axis=1)
    dQs = np.take(dQ, idx, axis=1)
    oddP = np.argmax(np.take(sP, idx, axis=1) == (cP[idx] == 1), axis=0)
    oddQ = np.argmax(np.take(sQ, idx, axis=1) == (cQ[idx] == 1), axis=0)
    x1, x2 = _cut(Ps, dPs, oddP)
    y1, y2 = _cut(Qs, dQs, oddQ)

    # All four points lie on the line where the two planes meet; compare the
    # two intervals along that line.
    D = _cross(np.take(nP, idx, axis=1), np.take(nQ, idx, axis=1))
    lD2 = _dot(D, D)
    tx1 = _dot(x1, D)
    tx2 = _dot(x2, D)
    ty1 = _dot(y1, D)
    ty2 = _dot(y2, D)
    xsw = tx1 > tx2
    ysw = ty1 > ty2
    xlo = np.where(xsw, tx2, tx1)
    xhi = np.where(xsw, tx1, tx2)
    ylo = np.where(ysw, ty2, ty1)
    yhi = np.where(ysw, ty1, ty2)
    lo = np.maximum(xlo, ylo)
    hi = np.minimum(xhi, yhi)
    ok = (lD2 > 1e-20) & (lo <= hi + eps * np.sqrt(lD2))
    if not ok.any():
        return hit, np.zeros((0, 2, 3))

    x_lo = np.where(xsw, x2, x1)
    x_hi = np.where(xsw, x1, x2)
    y_lo = np.where(ysw, y2, y1)
    y_hi = np.where(ysw, y1, y2)
    p_lo = np.where(xlo >= ylo, x_lo, y_lo)
    p_hi = np.where(xhi <= yhi, x_hi, y_hi)

    hit[idx[ok]] = True
    seg = np.empty((int(ok.sum()), 2, 3))
    seg[:, 0, :] = p_lo[:, ok].T
    seg[:, 1, :] = p_hi[:, ok].T
    return hit, seg


def _vertex_face(V, T, ET):
    """Squared distance from the corners of V to the triangles T.

    Only reports a finite distance where the corner projects inside T; the
    remaining cases are covered by the edge/edge tests.
    Returns d2 (3 corners, K) and the projected points (3, 3 corners, K).
    """
    n = _cross(ET[:, 0], -ET[:, 2])                    # (3, K)
    nn = _dot(n, n)
    nn_s = np.maximum(nn, _TINY)
    w = V - T[:, 0][:, None, :]                        # (3, corners, K)
    dn = _dot(w, n[:, None, :])                        # (corners, K)
    proj = V - (dn / nn_s) * n[:, None, :]
    u = proj[:, :, None, :] - T[:, None, :, :]         # (3, corners, edges, K)
    cr = _cross(ET[:, None, :, :], u)
    sg = _dot(cr, n[:, None, None, :])                 # (corners, edges, K)
    inside = (sg >= -1e-9 * nn).all(axis=1) & (nn > _TINY)
    d2 = np.where(inside, dn * dn / nn_s, np.inf)
    return d2, proj


def tri_tri_distance(P, Q):
    """Minimum distance between triangle pairs that do not pierce each other.

    Parameters: P, Q (3, 3, K) float64.
    Returns d2 (K,) squared distance, cp (3, K) closest point on P, cq (3, K).
    """
    K = P.shape[2]
    if K == 0:
        z = np.zeros((3, 0))
        return np.zeros(0), z, z

    EP = P[:, _NEXT] - P                                # (3, edges, K)
    EQ = Q[:, _NEXT] - Q

    # --- nine edge/edge pairs: closest points of two segments -------------
    r = P[:, :, None, :] - Q[:, None, :, :]             # (3, i, j, K)
    e1 = EP[:, :, None, :]
    e2 = EQ[:, None, :, :]
    a = _dot(EP, EP)[:, None, :]                        # (i, 1, K)
    e = _dot(EQ, EQ)[None, :, :]                        # (1, j, K)
    b = _dot(e1, e2)                                    # (i, j, K)
    c = _dot(e1, r)
    f = _dot(e2, r)
    ae = a * e
    denom = ae - b * b
    a_s = np.maximum(a, _TINY)
    e_s = np.maximum(e, _TINY)
    with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
        s = np.where(denom > 1e-12 * ae,
                     (b * f - c * e) / np.where(denom > 0.0, denom, 1.0), 0.0)
        np.clip(s, 0.0, 1.0, out=s)
        t = (b * s + f) / e_s
        tc = np.clip(t, 0.0, 1.0)
        s = np.where(t != tc, np.clip((b * tc - c) / a_s, 0.0, 1.0), s)
    diff = r + s * e1
    diff -= tc * e2
    dd = _dot(diff, diff)                               # (i, j, K)

    # --- six corner/face pairs ---------------------------------------------
    d2_pq, proj_pq = _vertex_face(P, Q, EQ)             # corners of P on Q
    d2_qp, proj_qp = _vertex_face(Q, P, EP)

    allc = np.concatenate([dd.reshape(9, K), d2_pq, d2_qp], axis=0)   # (15, K)
    j = np.argmin(allc, axis=0)
    d2min = np.take_along_axis(allc, j[None, :], axis=0)[0]

    # closest points of the winning feature pair
    je = np.minimum(j, 8)
    ei = je // 3
    ej = je % 3
    k = np.arange(K)
    s_w = s[ei, ej, k]
    t_w = tc[ei, ej, k]
    cp = _pick(P, ei) + s_w * _pick(EP, ei)
    cq = _pick(Q, ej) + t_w * _pick(EQ, ej)
    m = (j >= 9) & (j < 12)
    if m.any():
        v = np.clip(j - 9, 0, 2)
        cp = np.where(m, _pick(P, v), cp)
        cq = np.where(m, _pick(proj_pq, v), cq)
    m = j >= 12
    if m.any():
        v = np.clip(j - 12, 0, 2)
        cq = np.where(m, _pick(Q, v), cq)
        cp = np.where(m, _pick(proj_qp, v), cp)
    return d2min, cp, cq


def plane_gap2(P, Q):
    """Cheap lower bound on the squared distance from the two triangle planes:
    if all corners of one triangle are on one side of the other's plane, they
    are at least that far apart."""
    def one(T, O):
        n = _cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
        nn = np.maximum(_dot(n, n), _TINY)
        d = _dot(O - T[:, 0][:, None, :], n[:, None, :])
        lo = d.min(axis=0)
        hi = d.max(axis=0)
        g = np.where(lo > 0.0, lo, np.where(hi < 0.0, -hi, 0.0))
        return g * g / nn
    return np.maximum(one(P, Q), one(Q, P))
