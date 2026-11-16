---
id: continuum-kinematics
title: "Continuum Kinematics: Deformation Gradient and Strain Measures"
domain: computational-mechanics
subdomain: kinematics
status: established
source: human
tags:
  - kinematics
  - deformation-gradient
  - strain
  - finite-strain
  - polar-decomposition
confidence: 0.95
akms_schema: v2
edges:
  - to: elastic-isotropic
    type: feeds-into
    weight: 0.7
  - to: j2-plasticity
    type: feeds-into
    weight: 0.7
context_size: medium
reading_priority: full
---

# Continuum Kinematics: Deformation Gradient and Strain Measures

## Summary
[PLACEHOLDER — content TBD (TBD-3)] Continuum kinematics maps a
reference body Ω₀ to its current configuration Ω via the deformation
gradient F = ∂x/∂X.  Polar decomposition F = R U = V R separates
rigid rotation from pure stretch.  Objective strain measures (E, e, ε)
are derived from F and reduce to the infinitesimal strain ε = ½(∇u + ∇uᵀ)
at small deformations.

## 1. Core Concept

Deformation gradient:  F_iA = ∂x_i / ∂X_A,  J = det(F) > 0

Right Cauchy-Green tensor:  C = FᵀF  (symmetric, positive-definite)
Left Cauchy-Green tensor:   B = FFᵀ

Green-Lagrange strain:  E = ½(C − I) = ½(FᵀF − I)
  → linearises to ε = ½(∇u + ∇uᵀ) for small displacements

Euler-Almansi strain:   e = ½(I − B⁻¹)  (current configuration)

Polar decomposition:  F = R U = V R
  where R ∈ SO(3), U = √C (right stretch), V = √B (left stretch)

## 2. Known Pitfalls

- **Objectivity**: stress rates (Jaumann, Truesdell) must be objective;
  naive time derivatives of Cauchy stress are not frame-indifferent.
- **J ≤ 0**: a negative Jacobian signals element inversion; the formulation
  breaks down and typically indicates excessive distortion.

## 3. References
- Holzapfel (2000). *Nonlinear Solid Mechanics*, Ch. 2.
- Bonet & Wood (2008). *Nonlinear Continuum Mechanics for FEM*, Ch. 3.
