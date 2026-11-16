---
id: finite-differences-basics
title: "Finite Difference Approximations"
domain: numerical-methods
subdomain: discretisation
status: established
source: human
tags:
  - finite-differences
  - discretisation
  - truncation-error
  - stencils
confidence: 0.95
akms_schema: v2
edges: []
context_size: small
reading_priority: full
---

# Finite Difference Approximations

## Summary
[PLACEHOLDER — content TBD (TBD-3)] Finite differences replace
continuous derivatives with algebraic ratios of function values at
discrete grid points.  Taylor-series analysis quantifies the truncation
error; centred differences achieve O(h²) accuracy vs O(h) for one-sided
stencils.

## 1. Core Concept

First derivative — forward, backward, centred:

    f'(x) ≈ [f(x+h) − f(x)] / h             O(h)   forward
    f'(x) ≈ [f(x) − f(x−h)] / h             O(h)   backward
    f'(x) ≈ [f(x+h) − f(x−h)] / (2h)        O(h²)  centred

Second derivative (centred):

    f''(x) ≈ [f(x+h) − 2f(x) + f(x−h)] / h²  O(h²)

## 2. Known Pitfalls

- **Round-off vs truncation**: making h too small causes catastrophic
  cancellation; optimal h ≈ √ε_machine for first derivatives.
- **Boundary stencils**: centred stencils need ghost cells or one-sided
  stencils at boundaries.

## 3. References
- LeVeque (2007). *Finite Difference Methods for ODEs and PDEs*, Ch. 1.
- Strikwerda (2004). *Finite Difference Schemes and PDEs*, Ch. 1.
