"""Checks the vectorised triangle routines against the slow references."""

import os
import sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'build_check', 'core'))
sys.path.insert(0, HERE)

import tritri  # noqa: E402
import ref  # noqa: E402


def run(P, Q, eps):
    """Call the vectorised routines on (K, 3, 3) arrays; results back in that layout."""
    Ps, Qs = tritri.to_soa(P), tritri.to_soa(Q)
    hit, seg_hit = tritri.tri_tri_intersect(Ps, Qs, eps)
    seg = np.zeros((len(P), 2, 3))
    seg[hit] = seg_hit
    d2, cp, cq = tritri.tri_tri_distance(Ps, Qs)
    lb = tritri.plane_gap2(Ps, Qs)
    assert (lb <= d2 * (1 + 1e-9) + 1e-18).all(), 'plane bound exceeds the true distance'
    return hit, seg, d2, cp.T, cq.T


def random_pairs(rng, k, spread):
    P = rng.random((k, 3, 3))
    Q = rng.random((k, 3, 3)) + rng.normal(0, spread, (k, 1, 3))
    return P, Q


def special_pairs():
    """Hand-made degenerate and touching configurations: (P, Q, expected distance)."""
    out = []
    base = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float)
    # coplanar, overlapping
    out.append((base, base + [0.2, 0.2, 0.0], 0.0))
    # coplanar, identical
    out.append((base, base.copy(), 0.0))
    # coplanar, disjoint by 0.5 along x (closest: corner (1,0,0) to edge x=1.5)
    out.append((base, base + [1.5, 0.0, 0.0], 0.5))
    # parallel planes, stacked, gap 0.25
    out.append((base, base + [0.1, 0.1, 0.25], 0.25))
    # sharing one corner only
    out.append((base, np.array([[0, 0, 0], [-1, 0, 1], [0, -1, 1]], float), 0.0))
    # sharing an edge, folded
    out.append((base, np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1]], float), 0.0))
    # corner touching the face interior
    out.append((base, np.array([[0.2, 0.2, 0], [0.2, 0.2, 1], [0.6, 0.2, 1]], float), 0.0))
    # corner just above the face interior
    out.append((base, np.array([[0.2, 0.2, 0.01], [0.2, 0.2, 1], [0.6, 0.2, 1]], float), 0.01))
    # piercing through the middle
    out.append((base, np.array([[0.2, 0.2, -1], [0.2, 0.2, 1], [0.6, 0.2, 1]], float), 0.0))
    # crossing edges (edge/edge closest), gap 0.3
    out.append((np.array([[-1, 0, 0], [1, 0, 0], [0, 0, -1]], float),
                np.array([[0, -1, 0.3], [0, 1, 0.3], [0, 0, 1.3]], float), 0.3))
    # zero-area triangle (a segment) above a face
    out.append((base, np.array([[0.1, 0.1, 0.4], [0.3, 0.3, 0.4], [0.2, 0.2, 0.4]], float), 0.4))
    return out


def main():
    rng = np.random.default_rng(7)
    n_checked = 0
    worst = 0.0
    n_hit = 0
    for spread in (0.2, 0.6, 1.5):
        P, Q = random_pairs(rng, 1500, spread)
        hit, seg, d2, cp, cq = run(P, Q, 1e-12)
        for i in range(len(P)):
            r_hit = ref.tri_tri_hit(P[i], Q[i])
            assert bool(hit[i]) == r_hit, ('intersection mismatch', spread, i)
            if r_hit:
                n_hit += 1
                # both segment ends must lie on both triangles
                for e in (0, 1):
                    for T in (P[i], Q[i]):
                        c = ref.closest_pt_on_tri(seg[i, e], T[0], T[1], T[2])
                        assert np.linalg.norm(c - seg[i, e]) < 1e-9, ('segment off surface', i)
            else:
                rd = ref.tri_tri_dist(P[i], Q[i])
                got = float(np.sqrt(d2[i]))
                err = abs(got - rd)
                worst = max(worst, err)
                assert err < 1e-9, ('distance mismatch', spread, i, got, rd)
                # reported closest points must realise the distance and lie on the triangles
                assert abs(np.linalg.norm(cp[i] - cq[i]) - got) < 1e-9
                c = ref.closest_pt_on_tri(cp[i], P[i, 0], P[i, 1], P[i, 2])
                assert np.linalg.norm(c - cp[i]) < 1e-9
                c = ref.closest_pt_on_tri(cq[i], Q[i, 0], Q[i, 1], Q[i, 2])
                assert np.linalg.norm(c - cq[i]) < 1e-9
            n_checked += 1
    print(f'random pairs checked: {n_checked}  intersecting: {n_hit}  worst distance error: {worst:.2e}')

    sp = special_pairs()
    P = np.array([s[0] for s in sp])
    Q = np.array([s[1] for s in sp])
    hit, seg, d2, cp, cq = run(P, Q, 1e-9)
    for i, (_, _, want) in enumerate(sp):
        got = 0.0 if hit[i] else float(np.sqrt(d2[i]))
        assert abs(got - want) < 1e-9, ('special case', i, got, want)
        # symmetric call must agree
        h2, _, e2, _, _ = run(Q[i:i + 1], P[i:i + 1], 1e-9)
        got2 = 0.0 if h2[0] else float(np.sqrt(e2[0]))
        assert abs(got2 - want) < 1e-9, ('special case swapped', i, got2, want)
    print(f'special cases checked: {len(sp)}')
    print('OK')


if __name__ == '__main__':
    main()
