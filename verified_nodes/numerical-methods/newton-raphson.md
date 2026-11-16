---
id: newton-raphson
title: "Newton-Raphson Nonlinear Solver"
domain: numerical-methods
subdomain: nonlinear-solvers
status: established
source: human
tags:
  - newton-raphson
  - nonlinear-solver
  - quadratic-convergence
  - tangent-stiffness
confidence: 0.95
akms_schema: v2
edges:
  - to: finite-differences-basics
    type: requires
    weight: 0.6
context_size: small
reading_priority: full
---

# Newton-Raphson Nonlinear Solver

## Summary
[PLACEHOLDER — content TBD (TBD-3)] Newton-Raphson iterates
x_{k+1} = x_k − J(x_k)^{-1} F(x_k) to find roots of a residual F(x) = 0.
Under sufficient smoothness and a good initial guess it converges
quadratically — the number of correct digits roughly doubles each iteration.
In FEM this becomes the global tangent stiffness solve K_T Δu = −R.

## 1. Core Concept

Algorithm:
1. Evaluate residual R_k = F(x_k)
2. Assemble tangent J_k = ∂F/∂x |_{x_k}
3. Solve linear system J_k δx = −R_k
4. Update x_{k+1} = x_k + δx
5. Check ||R_{k+1}|| / ||R_0|| < tol; if not, go to 1.

Quadratic convergence: ||e_{k+1}|| ≤ C ||e_k||²  (near the root)

## 2. Known Pitfalls

- **Poor initial guess**: far from the root Newton-Raphson can diverge or
  cycle; arc-length or load-stepping is needed for snap-through problems.
- **Singular tangent**: at a limit point det(K_T) = 0; use arc-length or
  displacement control.

## 3. References
- Crisfield (1991). *Non-linear Finite Element Analysis*, Vol. 1, Ch. 2.
- Nocedal & Wright (2006). *Numerical Optimization*, Ch. 11.
