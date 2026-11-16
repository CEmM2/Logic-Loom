---
id: elastic-isotropic
title: "Isotropic Linear Elasticity"
domain: computational-mechanics
subdomain: elasticity
status: established
source: human
tags:
  - elasticity
  - isotropic
  - hooke
  - lame
confidence: 0.95
akms_schema: v2
edges:
  - to: j2-plasticity
    type: feeds-into
    weight: 0.8
context_size: medium
reading_priority: full
---

# Isotropic Linear Elasticity

## Summary
[PLACEHOLDER — content TBD (TBD-3)] Isotropic linear elasticity describes
the mechanical response of materials whose stiffness is the same in every
direction.  Two scalar parameters — the Lamé constants (λ, μ) or equivalently
Young's modulus E and Poisson's ratio ν — fully characterise the constitutive
law σ = λ tr(ε) I + 2μ ε.

## 1. Core Concept

The constitutive relation for an isotropic linear-elastic solid in index notation is

    σ_ij = λ ε_kk δ_ij + 2μ ε_ij

where λ and μ are the Lamé parameters.  In engineering form with Young's
modulus E and Poisson's ratio ν:

    λ = E ν / ((1+ν)(1−2ν)),  μ = E / (2(1+ν))

## 2. Known Pitfalls

- **Incompressibility limit**: as ν → 0.5 the bulk modulus K → ∞ and λ → ∞;
  standard displacement formulations lock — use mixed (u/p) elements.
- **Plane-stress vs plane-strain**: the effective stiffness matrix differs;
  confusing the two gives silent errors in thin-plate or long-tunnel problems.

## 3. References
- Timoshenko & Goodier (1951). *Theory of Elasticity*, 3rd ed.
- Belytschko et al. (2014). *Nonlinear Finite Elements*, Ch. 5.
