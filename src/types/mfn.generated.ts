// ============================================================================
// AUTO-GENERATED FROM TYPEDB SCHEMA (schema_unified.tql)
// DO NOT EDIT MANUALLY
//
// Generated: 2026-02-07
// Schema version: 3.0.0
//
// Architecture: Provisions are PURE ANCHORS (zero extracted values).
// Three data channels:
//   Channel 1: provision_has_answer    → scalar  (boolean, number, string)
//   Channel 2: concept_applicability   → multiselect (closed set from seed)
//   Channel 3: typed entities          → structured (variable-length)
// ============================================================================

// ============================================================================
// PROVENANCE
// ============================================================================

export type Confidence = 'high' | 'medium' | 'low';

export interface Provenance {
  section_reference?: string;
  source_text?: string;
  source_page?: number;
  source_section?: string;
  confidence?: Confidence;
  extracted_at?: string;
}

// ============================================================================
// CHANNEL 1: SCALAR ANSWERS (provision_has_answer relation)
//
// THE canonical store for all scalar extracted values.
// One relation instance per (provision, question) pair.
// ============================================================================

export interface ProvisionAnswer {
  answer_id: string;
  question_id: string;
  answer_boolean?: boolean;
  answer_integer?: number;
  answer_double?: number;
  answer_string?: string;
  answer_date?: string;
  source_text?: string;
  source_page?: number;
  source_section?: string;
  confidence?: Confidence;
  extracted_at?: string;
}

export type AnswerType =
  | 'boolean'
  | 'integer'
  | 'double'
  | 'percentage'
  | 'currency'
  | 'string'
  | 'number'
  | 'multiselect'
  | 'entity';

export type CovenantType = 'MFN' | 'RP';

/** Map of question_id → typed answer for a single provision */
export interface ProvisionAnswerMap {
  [questionId: string]: ProvisionAnswer;
}

// ============================================================================
// CHANNEL 2: CONCEPT TYPES (concept_applicability relation)
// ============================================================================

export type ApplicabilityStatus = 'INCLUDED' | 'EXCLUDED';

// MFN concept types
export type MFNConceptType =
  | 'facility_prong'
  | 'yield_component'
  | 'fee_exclusion'
  | 'lien_priority'
  | 'governance_concept'
  | 'sunset_type'
  | 'covered_person'
  | 'repurchase_trigger'
  | 'loophole_type'
  | 'protection_type';

// RP concept types
export type RPConceptType =
  | 'dividend_definition_element'
  | 'ip_type'
  | 'transfer_type'
  | 'blocker_binding_entity'
  | 'blocker_timing'
  | 'investment_basket'
  | 'default_type'
  | 'reallocation_target';

// RP expanded concept types
export type RPExpandedConceptType =
  | 'builder_reduction_type'
  | 'intercompany_recipient_type'
  | 'tax_group_type'
  | 'overhead_cost_type'
  | 'transaction_cost_type'
  | 'equity_award_type'
  | 'rdp_payment_type'
  | 'reallocation_reduction_type'
  | 'exempt_sale_type'
  | 'unsub_distribution_condition'
  | 'ratio_required_basket'
  | 'no_default_basket';

export type ConceptType = MFNConceptType | RPConceptType | RPExpandedConceptType;

export interface ConceptApplicability {
  concept_id: string;
  name?: string;
  applicability_status: ApplicabilityStatus;
  source_text?: string;
  source_page?: number;
  source_section?: string;
  confidence?: Confidence;
}

// ============================================================================
// CHANNEL 3: EXTRACTED ENTITIES
// ============================================================================

// ── Shared provenance fields (inherited by all extracted entities) ────────

interface EntityProvenance {
  section_reference?: string;
  source_page?: number;
  source_text?: string;
  confidence?: Confidence;
}

// ────────────────────────────────────────────────────────────────────────────
// RP BASKETS (7 subtypes of rp_basket → provision_has_basket)
// ────────────────────────────────────────────────────────────────────────────

export type DefaultCondition =
  | 'any_default'
  | 'payment_default_only'
  | 'specified_defaults'
  | 'none';

export interface RPBasketBase extends EntityProvenance {
  basket_id: string;
  default_condition?: DefaultCondition;
}

/** Builder Basket (Available Amount / Cumulative Amount) */
export interface BuilderBasket extends RPBasketBase {
  start_date_language?: string;
  uses_greatest_of_tests?: boolean;
  sources?: BuilderBasketSource[];
}

/** Ratio Basket — unlimited dividends if leverage ratio meets threshold */
export interface RatioBasket extends RPBasketBase {
  ratio_threshold?: number;
  ratio_type?: string;
  is_unlimited_if_met?: boolean;
  has_no_worse_test?: boolean;
  no_worse_threshold?: number;
  test_date_type?: string;
  lct_treatment_available?: boolean;
  pro_forma_basis?: boolean;
}

/** General RP Basket — fixed dollar basket */
export interface GeneralRPBasket extends RPBasketBase {
  basket_amount_usd?: number;
  basket_grower_pct?: number;
  is_per_annum?: boolean;
}

/** Management Equity Basket — repurchasing management/employee equity */
export interface ManagementEquityBasket extends RPBasketBase {
  annual_cap_usd?: number;
  annual_cap_pct_ebitda?: number;
  cap_uses_greater_of?: boolean;
  carryforward_permitted?: boolean;
  carryforward_max_years?: number;
  eligible_person_scope?: string;
}

/** Tax Distribution Basket — pass-through entity tax payments */
export interface TaxDistributionBasket extends RPBasketBase {
  standalone_taxpayer_limit?: boolean;
  hypothetical_tax_rate?: number;
  tax_sharing_permitted?: boolean;
  estimated_taxes_permitted?: boolean;
}

/** Holdco Overhead Basket — distributions for holding company expenses */
export interface HoldcoOverheadBasket extends RPBasketBase {
  annual_cap_usd?: number;
  covers_management_fees?: boolean;
  covers_admin_expenses?: boolean;
  covers_franchise_taxes?: boolean;
  management_fee_recipient_scope?: string;
  requires_arms_length?: boolean;
  requires_board_approval?: boolean;
}

/** Equity Award Basket — equity award settlement payments */
export interface EquityAwardBasket extends RPBasketBase {
  annual_cap_usd?: number;
  covers_cashless_exercise?: boolean;
  covers_tax_withholding?: boolean;
  carryforward_permitted?: boolean;
}

export type RPBasketType =
  | BuilderBasket
  | RatioBasket
  | GeneralRPBasket
  | ManagementEquityBasket
  | TaxDistributionBasket
  | HoldcoOverheadBasket
  | EquityAwardBasket;

// ────────────────────────────────────────────────────────────────────────────
// BUILDER BASKET SOURCES (8 polymorphic subtypes → basket_has_source)
// ────────────────────────────────────────────────────────────────────────────

export type BuilderSourceType =
  | 'starter_amount'
  | 'cni'
  | 'ecf'
  | 'ebitda_fc'
  | 'equity_proceeds'
  | 'investment_returns'
  | 'asset_proceeds'
  | 'debt_conversion';

export interface BuilderBasketSourceBase extends EntityProvenance {
  source_id: string;
  source_type: BuilderSourceType;
  not_otherwise_applied?: boolean;
}

export interface StarterAmountSource extends BuilderBasketSourceBase {
  source_type: 'starter_amount';
  dollar_amount?: number;
  ebitda_percentage?: number;
  uses_greater_of?: boolean;
}

export interface CNISource extends BuilderBasketSourceBase {
  source_type: 'cni';
  percentage?: number;
  is_primary_test?: boolean;
}

export interface ECFSource extends BuilderBasketSourceBase {
  source_type: 'ecf';
  retained_ecf_formula?: string;
  lookback_period?: string;
  lookback_quarters?: number;
}

export interface EBITDAFCSource extends BuilderBasketSourceBase {
  source_type: 'ebitda_fc';
  fc_multiplier?: number;
}

export interface EquityProceedsSource extends BuilderBasketSourceBase {
  source_type: 'equity_proceeds';
  percentage?: number;
  excludes_cure_contributions?: boolean;
  excludes_disqualified_stock?: boolean;
}

export interface InvestmentReturnsSource extends BuilderBasketSourceBase {
  source_type: 'investment_returns';
}

export interface AssetProceedsSource extends BuilderBasketSourceBase {
  source_type: 'asset_proceeds';
  percentage?: number;
}

export interface DebtConversionSource extends BuilderBasketSourceBase {
  source_type: 'debt_conversion';
}

export type BuilderBasketSource =
  | StarterAmountSource
  | CNISource
  | ECFSource
  | EBITDAFCSource
  | EquityProceedsSource
  | InvestmentReturnsSource
  | AssetProceedsSource
  | DebtConversionSource;

// ────────────────────────────────────────────────────────────────────────────
// RDP BASKETS (5 subtypes of rdp_basket → provision_has_rdp_basket)
// Restricted Debt Payments — SEPARATE hierarchy from RP baskets
// ────────────────────────────────────────────────────────────────────────────

export interface RDPBasketBase extends EntityProvenance {
  basket_id: string;
  default_condition?: DefaultCondition;
}

/** Refinancing RDP — prepay junior debt with new debt at same/lower priority */
export interface RefinancingRDPBasket extends RDPBasketBase {
  requires_same_or_lower_priority?: boolean;
  requires_same_or_later_maturity?: boolean;
  requires_no_increase_in_principal?: boolean;
  permits_refinancing_with_equity?: boolean;
  requires_qualified_stock_only?: boolean;
  not_otherwise_applied?: boolean;
  subject_to_intercreditor?: boolean;
}

/** General RDP — fixed dollar basket for general RDP capacity */
export interface GeneralRDPBasket extends RDPBasketBase {
  basket_amount_usd?: number;
  basket_grower_pct?: number;
}

/** Ratio RDP — leverage test unlocks unlimited RDP */
export interface RatioRDPBasket extends RDPBasketBase {
  ratio_threshold?: number;
  ratio_type?: string;
  is_unlimited_if_met?: boolean;
  test_date_type?: string;
  pro_forma_basis?: boolean;
  uses_closing_ratio_alternative?: boolean;
}

/** Builder RDP — cumulative amount basket, may share with RP builder */
export interface BuilderRDPBasket extends RDPBasketBase {
  shares_with_rp_builder?: boolean;
  subject_to_intercreditor?: boolean;
}

/** Equity-Funded RDP — RDP funded by fresh equity contributions */
export interface EquityFundedRDPBasket extends RDPBasketBase {
  requires_qualified_stock_only?: boolean;
  requires_cash_common_equity?: boolean;
  not_otherwise_applied?: boolean;
}

export type RDPBasketType =
  | RefinancingRDPBasket
  | GeneralRDPBasket
  | RatioRDPBasket
  | BuilderRDPBasket
  | EquityFundedRDPBasket;

// ────────────────────────────────────────────────────────────────────────────
// SWEEP TIERS (variable-length → provision_has_sweep_tier)
// ────────────────────────────────────────────────────────────────────────────

export interface SweepTier extends EntityProvenance {
  tier_id: string;
  leverage_threshold: number;
  sweep_percentage: number;
  is_highest_tier?: boolean;
}

// ────────────────────────────────────────────────────────────────────────────
// J.CREW BLOCKER + EXCEPTIONS (provision_has_blocker, blocker_has_exception)
// ────────────────────────────────────────────────────────────────────────────

export type BlockerExceptionType =
  | 'nonexclusive_license_exception'
  | 'intercompany_exception'
  | 'immaterial_ip_exception'
  | 'fair_value_exception'
  | 'ordinary_course_exception';

export interface BlockerException extends EntityProvenance {
  exception_id: string;
  exception_type: BlockerExceptionType;
  scope_limitation?: string;
}

export interface JCrewBlocker extends EntityProvenance {
  blocker_id: string;
  covers_transfer?: boolean;
  covers_designation?: boolean;
  covers_ip?: boolean;
  covers_material_assets?: boolean;
  covers_exclusive_licensing?: boolean;
  covers_nonexclusive_licensing?: boolean;
  covers_pledge?: boolean;
  covers_abandonment?: boolean;
  binds_loan_parties?: boolean;
  binds_restricted_subs?: boolean;
  is_sacred_right?: boolean;
  exceptions?: BlockerException[];
}

// ────────────────────────────────────────────────────────────────────────────
// UNRESTRICTED SUBSIDIARY DESIGNATION (provision_has_unsub)
// ────────────────────────────────────────────────────────────────────────────

export interface UnsubDesignation extends EntityProvenance {
  designation_id: string;
  requires_no_default?: boolean;
  requires_board_approval?: boolean;
  dollar_cap_usd?: number;
  pct_cap_assets?: number;
  redesignation_permitted?: boolean;
}

// ────────────────────────────────────────────────────────────────────────────
// DE MINIMIS THRESHOLDS (provision_has_de_minimis)
// ────────────────────────────────────────────────────────────────────────────

export interface DeMinimisThreshold extends EntityProvenance {
  threshold_id: string;
  threshold_type?: 'individual' | 'annual';
  threshold_amount_usd?: number;
}

// ────────────────────────────────────────────────────────────────────────────
// BASKET REALLOCATION (provision_has_reallocation)
// ────────────────────────────────────────────────────────────────────────────

export interface BasketReallocation extends EntityProvenance {
  reallocation_id: string;
  reallocation_source?: string;
  reallocation_amount_usd?: number;
  is_bidirectional?: boolean;
  reduces_source_basket?: boolean;
  reduction_is_dollar_for_dollar?: boolean;
  reduction_while_outstanding_only?: boolean;
}

// ────────────────────────────────────────────────────────────────────────────
// INVESTMENT PATHWAYS (provision_has_pathway)
//
// J.Crew chain analysis: LP → Non-Guarantor RS → Unsub
// Graph traversal detects open chains that flat booleans cannot.
// ────────────────────────────────────────────────────────────────────────────

export interface InvestmentPathway extends EntityProvenance {
  pathway_id: string;
  pathway_source_type: string;
  pathway_target_type: string;
  cap_dollar_usd?: number;
  cap_pct_total_assets?: number;
  cap_uses_greater_of?: boolean;
  is_uncapped?: boolean;
  can_stack_with_other_baskets?: boolean;
}

// ============================================================================
// QUALIFICATION & CITATION
// ============================================================================

export type QualificationType =
  | 'exception'
  | 'condition'
  | 'threshold'
  | 'scope_limitation';

export interface Qualification {
  qualification_id: string;
  qualification_type: QualificationType;
  description?: string;
  source_text?: string;
  source_page?: number;
}

export type CitationContext = 'definition' | 'covenant' | 'exception';

export interface SourceCitation {
  citation_id: string;
  source_text?: string;
  source_page?: number;
  citation_context?: CitationContext;
}

export type CrossReferenceType =
  | 'limited_by'
  | 'enhanced_by'
  | 'depends_on'
  | 'conflicts_with';

export interface ProvisionCrossReference {
  source_provision_id: string;
  target_provision_id: string;
  cross_reference_type: CrossReferenceType;
  cross_reference_explanation?: string;
  source_text?: string;
  source_page?: number;
}

// ============================================================================
// PROVISION TYPES (Pure Anchors)
//
// Provisions own ZERO extracted values. Only identity + computed pattern flags.
// All extracted data accessed via the three channels.
// ============================================================================

export interface ProvisionBase {
  provision_id: string;
  section_reference?: string;
  source_page?: number;
  extracted_at?: string;
}

export interface MFNProvision extends ProvisionBase {
  yield_exclusion_pattern_detected?: boolean;
  answers?: ProvisionAnswerMap;
  applicabilities?: Record<string, ConceptApplicability[]>;
}

export interface RPProvision extends ProvisionBase {
  jcrew_pattern_detected?: boolean;
  serta_pattern_detected?: boolean;
  collateral_leakage_pattern_detected?: boolean;
  answers?: ProvisionAnswerMap;
  applicabilities?: Record<string, ConceptApplicability[]>;
  // Channel 3 entities
  baskets?: RPBasketType[];
  rdp_baskets?: RDPBasketType[];
  blocker?: JCrewBlocker;
  unsub_designation?: UnsubDesignation;
  sweep_tiers?: SweepTier[];
  de_minimis_thresholds?: DeMinimisThreshold[];
  reallocations?: BasketReallocation[];
  investment_pathways?: InvestmentPathway[];
}

// ============================================================================
// DEAL
// ============================================================================

export interface Deal {
  deal_id: string;
  deal_name?: string;
  borrower_name?: string;
  deal_date?: string;
  deal_amount?: number;
  deal_currency?: string;
  deal_type?: string;
  facility_type?: string;
  created_at?: string;
  updated_at?: string;
}

// ============================================================================
// ONTOLOGY
// ============================================================================

export interface OntologyQuestion {
  question_id: string;
  question_number: number;
  question_text: string;
  description?: string;
  answer_type: AnswerType;
  ui_display_type?: string;
  is_required?: boolean;
  default_value?: string;
  display_order?: number;
  covenant_type: CovenantType;
  extraction_prompt?: string;
}

export interface OntologyCategory {
  category_id: string;
  name: string;
  description?: string;
  display_order: number;
  questions?: OntologyQuestion[];
}

// ============================================================================
// RP EXTRACTION OUTPUT (matches extraction_output_v4.py)
// ============================================================================

export interface RPExtractionV4 {
  // RP Baskets (7 subtypes)
  builder_basket?: BuilderBasket;
  ratio_basket?: RatioBasket;
  general_rp_basket?: GeneralRPBasket;
  management_equity_basket?: ManagementEquityBasket;
  tax_distribution_basket?: TaxDistributionBasket;
  holdco_overhead_basket?: HoldcoOverheadBasket;
  equity_award_basket?: EquityAwardBasket;
  // J.Crew Blocker
  jcrew_blocker?: JCrewBlocker;
  // Unrestricted Subsidiary
  unsub_designation?: UnsubDesignation;
  // Sweep & Prepayment
  sweep_tiers?: SweepTier[];
  de_minimis_thresholds?: DeMinimisThreshold[];
  // Reallocation
  reallocations?: BasketReallocation[];
  // RDP Baskets (5 subtypes — separate hierarchy)
  refinancing_rdp_basket?: RefinancingRDPBasket;
  general_rdp_basket?: GeneralRDPBasket;
  ratio_rdp_basket?: RatioRDPBasket;
  builder_rdp_basket?: BuilderRDPBasket;
  equity_funded_rdp_basket?: EquityFundedRDPBasket;
  // Investment Pathways
  investment_pathways?: InvestmentPathway[];
  // Metadata
  extraction_version?: string;
  extraction_confidence?: Confidence;
}
