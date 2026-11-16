---
id: j2-plasticity
title: "J2 (von Mises) Plasticity"
domain: computational-mechanics
subdomain: plasticity
status: established
source: human
tags:
  - plasticity
  - j2
  - von-mises
  - return-mapping
  - isotropic-hardening
confidence: 0.95
akms_schema: v2
edges:
  - to: elastic-isotropic
    type: requires
    weight: 0.9
context_size: medium
reading_priority: full
---

# J2 (von Mises) Plasticity

## Summary
[PLACEHOLDER — content TBD (TBD-3)] J2 plasticity is the standard
pressure-independent isotropic yield model for metals.  The yield surface
is a cylinder in principal-stress space centred on the hydrostatic axis;
yielding is governed by the second deviatoric-stress invariant J2 = ½ s:s.
A radial-return algorithm integrates the constitutive update exactly.

## 1. Core Concept

Yield function:  f(σ, κ) = √(3 J2) − (σ_y + H κ)  ≤ 0

where J2 = ½ s_ij s_ij, s = σ − (tr σ/3) I is the deviatoric stress,
σ_y is the initial yield stress, H is the isotropic hardening modulus, and
κ is the accumulated plastic strain.

Return-mapping (radial return):
1. Elastic trial: σ_tr = σ_n + C : Δε
2. If f(σ_tr, κ_n) ≤ 0 → elastic step, accept trial state.
3. Else solve for Δγ: f(σ_tr − 3μ Δγ n_tr, κ_n + Δγ) = 0
4. Update: σ = σ_tr − 2μ Δγ n_tr, κ = κ_n + Δγ

## 2. Known Pitfalls

- **Volume locking in FEM**: J2 plasticity at ν ≈ 0.5 (near-incompressible)
  requires mixed or B-bar elements.
- **No pressure dependence**: soils and rock need Drucker-Prager or
  cap-plasticity extensions.

## 3. References
- Simo & Hughes (1998). *Computational Inelasticity*, Ch. 1–3.
- de Souza Neto et al. (2008). *Computational Methods for Plasticity*, Ch. 7.
