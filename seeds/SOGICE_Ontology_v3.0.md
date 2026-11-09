# SurvivingSOGICE — Discourse Ontology v3.0

**University of Bergen · SurvivingSOGICE PhD Research Archive**
*Phase 0 Document · Merges Ontology v2.1 and v2.2 additions into a single authoritative reference*
*Companion documents: SOGICE_Lexicon_v2.0.md · PRD_v3.1.md · Entity_Registry_v1.1.md*

---

## Changelog from v2.1/v2.2

- **Merged** v2.1 and v2.2 additions supplement into a single document — no more split reading
- **Two-layer model** (Discourse + Practice-Regulatory) now integrated throughout
- **LegalDefinition and ExclusionClause** entity types now in Part IV alongside existing entity types
- **stance_profile** replaces cited_for_critique as primary field in tag instance (Part III)
- **Attestation tiers** for multilingual variants now in Part VII
- **New Practice tags:** Physical-Coercion, Verbal-Abuse-Humiliation (ILGA-Europe Intersections 2.0 alignment)
- **Evidence** category changed to one-or-more cardinality
- **Resolved open questions:** Q7 (Blanchard → Anti-Trans), Q11 (France harm threshold → model it), Q12 (new Practice tags → added)
- All v2.1 content preserved; v2.2 additions integrated into their logical sections

---

## What this document is

The formal structural spine of the SurvivingSOGICE platform ontology. Defines the conceptual architecture referenced by all downstream systems: ingestion pipeline, Sanity CMS schema, LLM system prompts, and every `ai_metadata.ontology_version` field in the database.

**This document does NOT contain term definitions.** Full definitions live in `SOGICE_Lexicon_v2.0.md`.

**How to use alongside the Lexicon:**
> Read this document to understand the classification structure, rules, and entity model.
> Read SOGICE_Lexicon_v2.0.md to identify and tag specific terminology.

---

## PART I — THE SEVEN CLUSTERS

The cluster is the fundamental unit of the ontology. Every rhetorical tag belongs to exactly one primary cluster. Cluster assignment is at the **tag level**, not the document level.

```
Tactic (broad rhetorical strategy)
  └── Cluster (concept group — Lexicon and visualization entry point)
        └── Term (specific phrase — Lexicon entry)
```

| # | Cluster | Core Question |
|---|---------|---------------|
| C1 | SSA-Rhetoric | Does it rename or reframe homosexuality as attraction, struggle, or lifestyle? |
| C2 | Pastoral-Coercion | Does it frame conversion as spiritual care, healing, or obedience? |
| C3 | Pseudo-Science | Does it misuse science, invent diagnoses, or fabricate origin theories? |
| C4 | Policy-Resistance | Does it oppose conversion therapy bans using rights or freedom arguments? |
| C5 | Anti-Trans/ROGD | Does it specifically target trans identity or gender-affirming care? |
| C6 | Anti-Gender | Does it frame LGBTQ+ rights as ideological takeover of natural order? |
| C7 | Pro-Trans-SOGICE | Does it oppose SOGICE for LGB people while promoting SOGICE-equivalent practices for trans people? |

### Cluster Definitions

**C1 — SSA-Rhetoric:** All language that renames, reframes, or repositions homosexuality, bisexuality, or same-sex attraction in ways that deny identity, imply pathology, or suggest mutability. Core move: substitution of self-determined identity language with euphemistic language keeping conversion intact. Tactics: Identity-Erasure, Rebranding-SOGICE (language). Boundary: SSA-Rhetoric is about naming; Pastoral-Coercion is about practice. LDS SGA variant belongs here.

**C2 — Pastoral-Coercion:** All language and practice in which religious frameworks are deployed to suppress, redirect, or eliminate LGBTQ+ identity, presented as spiritual care. The coercion is structural: a pastor who genuinely believes they are helping still operates here if their framework pathologizes identity and makes belonging conditional on change. Tactics: Gender-Essentialism (religious), Identity-Erasure (pastoral). Side B theology belongs here even without orientation change claims.

**C3 — Pseudo-Science:** All uses of scientific, clinical, or psychological language to provide false legitimacy for SOGICE claims. Defining feature: appropriation of scientific register for conclusions mainstream science has rejected. Tactics: False-Scientific-Authority, Social-Contagion-Myth, ROGD-Frame. Includes the Blanchard Typology as a complete sub-system (HSTS, AGP, androphilic/gynephilic).

**C4 — Policy-Resistance:** All rhetorical strategies to oppose, delay, limit, or circumvent legal prohibitions on conversion therapy. Core move: reframing bans as attacks on freedom, therapy, conscience, faith, or parental rights. Tactics: Policy-Resistance-Frame, Rebranding-SOGICE (legislative). Legal case tracking required.

**C5 — Anti-Trans/ROGD:** All strategies specifically targeting trans identity, gender-affirming care, trans-inclusive policies. Defining feature: denial or delegitimization of trans identity as such. Two sub-traditions: gender-critical/secular and evangelical/religious. Tactics: ROGD-Frame, Detrans-Propaganda, Sex-Rejection-Frame, Anti-Trans Rhetoric. Documents using Blanchard typology in clinical register that feed anti-trans arguments are classified here.

**C6 — Anti-Gender:** The European-dominant tradition framing LGBTQ+ rights as a coordinated ideological project ("gender ideology") threatening civilization. Roots: Vatican theological documents (1990s) spreading through Catholic, Protestant, nationalist, and far-right movements. Tactics: Anti-Gender Narrative, Anti-LGBT Conspiracy, Groomer-Panic, Dehumanizing-Language. Transnational genealogy: Vatican → Manif pour Tous/Agenda Europe → national movements.

**C7 — Pro-Trans-SOGICE:** Positions explicitly opposing SOGICE for LGB people while simultaneously promoting SOGICE-equivalent practices for trans people. Defining feature: asymmetric application of anti-SOGICE logic. Not simply anti-trans — it is the selective deployment of SOGICE critique to legitimate restrictions on trans healthcare. Key organizations: LGB Alliance, SEGM, Genspect, 4thWaveNow, Transgender Trend.

---

## PART II — THE TWO-LAYER MODEL

The ontology operates on two complementary layers:

**Layer A — Discourse Layer:** How SOGICE is framed in text. Clusters, tactics, terms, functions. Answers: *"How is this described and justified?"*

**Layer B — Practice-Regulatory Layer:** What is done, where, by whom, and how it is legally defined. Practices, legal definitions, exclusion clauses, harm outcomes. Answers: *"What is the practice, and what does the law say about it?"*

```
Document / Web page / Report
  |
  +-- Segment / Quote
        |
        +-- [Layer A] Term --> Cluster --> Tactic
        |
        +-- [Layer B] ConversionPractice --> Modality
                                         --> Setting
                                         --> LegalDefinition --> ExclusionClause
                                         --> HarmOutcome
```

The two layers are linked: a term (e.g., "Therapeutic Choice") *frames/justifies/euphemises* a ConversionPractice. This linkage makes the system queryable: "find all documents that invoke a Policy-Resistance frame to argue an ExclusionClause applies to their practice."

---

## PART III — TACTIC-TO-CLUSTER MAPPING

### Core Tactics

| Tactic | Primary Cluster | Secondary Cluster |
|--------|----------------|-------------------|
| Identity-Erasure | SSA-Rhetoric | Pastoral-Coercion |
| Rebranding-SOGICE (language) | SSA-Rhetoric | — |
| Rebranding-SOGICE (legislative) | Policy-Resistance | SSA-Rhetoric |
| Gender-Essentialism (religious) | Pastoral-Coercion | — |
| Gender-Essentialism (secular) | Anti-Trans/ROGD | — |
| False-Scientific-Authority | Pseudo-Science | — |
| Social-Contagion-Myth | Pseudo-Science | Anti-Trans/ROGD |
| ROGD-Frame | Pseudo-Science | Anti-Trans/ROGD |
| Detrans-Propaganda | Anti-Trans/ROGD | Pro-Trans-SOGICE |
| Sex-Rejection-Frame | Anti-Trans/ROGD | Pastoral-Coercion |
| Anti-Trans Rhetoric | Anti-Trans/ROGD | — |
| Policy-Resistance-Frame | Policy-Resistance | — |
| Anti-Gender Narrative | Anti-Gender | — |
| Anti-LGBT Conspiracy | Anti-Gender | — |
| Groomer-Panic | Anti-Gender | Anti-Trans/ROGD |
| Dehumanizing-Language | Anti-Gender | Anti-Trans/ROGD |

### Tactics Added in v2.1 (retained)

**`PastoralCoercion-LegislativeLoophole`**
- Primary: Pastoral-Coercion · Secondary: Policy-Resistance
- The explicit legislative framing that "pastoral support" or "spiritual care" should be exempt from conversion therapy bans. Operationalized in UK, Scottish, and Maltese ban opposition strategies. Distinct from Policy-Resistance-Frame in that it specifically deploys pastoral/religious language to carve exemptions rather than opposing bans wholesale.
- Key documents: UK Memorandum of Understanding debates; Scottish PE1817 submissions; Malta Grech acquittal (2026).

**`Operation-Gideon`**
- Primary: Policy-Resistance · Secondary: Anti-Gender
- Coordinated pan-European legal and political campaign by IFTCC (June 2025) to challenge conversion therapy bans. Includes litigation, legislative lobbying, and media campaigns. The name invokes the biblical Gideon — a warrior called by God. Functions as both a rhetorical frame and a network coordination device.
- Source: https://iftcc.org/operation-gideon

**`Parental-Rights-Trans`**
- Primary: Pro-Trans-SOGICE · Secondary: Policy-Resistance
- The asymmetric application of parental rights arguments specifically to trans youth, while not applying them to LGB youth. Distinct from the general Parental Rights Frame in its specific deployment against trans youth healthcare.
- Boundary: Parental Rights Frame is Policy-Resistance for all SOGICE. Parental-Rights-Trans is the Pro-Trans-SOGICE variant targeting only trans children.

---

## PART IV — TAG INSTANCE STRUCTURE

```json
{
  "tagType": "tactic | term | function | evidence | format | country | practice | harm | migration | network | actor",
  "tagValue": "exact string from controlled vocabulary",
  "cluster": "SSA-Rhetoric | Pastoral-Coercion | Pseudo-Science | Policy-Resistance | Anti-Trans/ROGD | Anti-Gender | Pro-Trans-SOGICE | Non-SOGICE",
  "confidence": "high | medium | low | uncertain",

  "stance_profile": "promotional | critical_advocacy | legal_administrative | research_clinical",
  "promotional_use": "boolean",

  "evidenceSnippets": ["direct quote under 30 words"],
  "rationale": "one sentence explanation",
  "reviewStatus": "pending | confirmed | rejected",

  "target_dimensions": ["sexual_orientation", "gender_identity", "gender_expression", "sex_characteristics"],
  "goal_verbs": ["change", "suppress", "repress", "discourage", "deter", "modify", "eliminate"],
  "jurisdiction": "ISO 3166-1 alpha-2",
  "instrument_ref": "LegalDefinition._id",
  "exclusion_argument": "boolean",
  "boundary_clause_ref": "ExclusionClause._id",

  "ai_metadata": {
    "model": "",
    "provider": "",
    "prompt_version": "",
    "ontology_version": "v3.0",
    "layer": "algorithmic | llm_primary | llm_validation | human"
  }
}
```

### Field notes

**`stance_profile`** replaces and extends the former `cited_for_critique` boolean:
- `promotional` — source uses term to advocate for SOGICE
- `critical_advocacy` — source uses term to critique or oppose SOGICE
- `legal_administrative` — term appears in a legal/regulatory document
- `research_clinical` — term appears in academic analysis

`cited_for_critique` retained as computed backwards-compatibility field: true when stance_profile is `critical_advocacy`, false otherwise. New records use `stance_profile` as authoritative.

**`contested_figure` flag (Person entities):** true for researchers whose work is cited by both sides. Requires `contested_figure_note`. Known cases: Kenneth Zucker, Robert Spitzer†, Lisa Littman, Ray Blanchard.

**Optional Practice-Regulatory fields** (`target_dimensions`, `goal_verbs`, `jurisdiction`, `instrument_ref`, `exclusion_argument`, `boundary_clause_ref`) apply only when a tag instance relates to a specific practice, jurisdiction, or legal instrument.

---

## PART V — ENTITY MODEL

Six entity types. Every entity exists once in the database.

### Person

```
id, name, former_names, role (founder | leader | influencer | therapist | pastor | survivor | researcher),
affiliated_organizations, country_of_operation, languages, public_profile,
contested_figure (boolean), contested_figure_note, source_documents
```

### Organization

```
id, name, former_names [{name, from_year, to_year}],
type (ministry | therapy_practice | advocacy | academic | media | government | network_node | pseudo-professional-body | political_party),
country, founded_year, dissolved_year, parent_organization, funding_sources,
affiliated_organizations, description, visibility, source_documents
```

### Law / Policy

```
id, name, country, year, status (enacted | defeated | pending | challenged | amended),
applies_to (ban | protection | restriction | definition), description, source_documents
```

### Event

```
id, name, type (conference | court_case | media_event | publication | legislation | network_formation | investigation),
date, actors_involved, description, historical_significance (high | medium | low), source_documents
```

### LegalDefinition

A jurisdiction-specific definitional statement operationalising conversion practices for enforcement or policy measurement. Different jurisdictions define conversion practices differently; these differences are themselves objects of analysis.

```
id, jurisdiction (ISO 3166-1 alpha-2 + subnational where needed),
instrument_type (law | regulation | guidance | resolution | policy),
instrument_name, year_enacted, year_in_force,
term_used (exact term as it appears in the law),
target_dimensions [sexual_orientation, gender_identity, gender_expression, sex_characteristics],
goal_verbs [change, suppress, repress, discourage, deter, modify, eliminate, align_to_assigned_sex],
scope_form ("any treatment" | "practices and efforts" | "repeated practices/behaviours/statements" | other),
harm_threshold (boolean + description),
consent_override (boolean),
age_specific_provisions (description),
modality_examples_listed (boolean),
exclusions [ExclusionClause._id],
status (enacted | defeated | pending | challenged | amended),
source_url
```

**Seeded European LegalDefinitions:**

| ID | Jurisdiction | Term | Year | Harm threshold | Consent override |
|---|---|---|---|---|---|
| LD-MT-2016 | Malta | prattiċi ta' konverżjoni | 2016 | No | Yes (vulnerable persons) |
| LD-DE-2020 | Germany | Konversionsbehandlung | 2020 | No | No |
| LD-FR-2022 | France | pratiques visant à modifier ou réprimer | 2022 | **YES** — health deterioration required | No |
| LD-GR-2022 | Greece | Πρακτικές μεταστροφής | 2022 | No | Consent of non-vulnerable adult required |
| LD-ES-2023 | Spain | métodos/programas/terapias de aversión, conversión o contracondicionamiento | 2023 | No | **YES** — prohibited even with consent |
| LD-BE-2023 | Belgium | pratique de conversion | 2023 | No | No |
| LD-NO-2024 | Norway | konverteringsterapi (§270) | 2024 | No — but adult offence requires "krenker" | No |
| LD-PT-2024 | Portugal | práticas de «conversão sexual» | 2024 | No | No |
| LD-COE-2026 | Council of Europe | conversion practices (PACE Res. 2643) | 2026 | No | No |

**Key notes:**
- **France:** Harm threshold means a single pastoral conversation cannot be prosecuted; only repeated patterns producing measurable harm qualify. Pro-SOGICE actors use this as a model argument for other countries. This should be modeled as a separate legal variant for Policy-Resistance analysis.
- **Spain:** Strongest formulation in Europe — banned even with consent, directly countering the Therapeutic Choice argument.
- **Norway:** Adult offence requires "krenker" (violates), enabling case-by-case boundary. Minor provision: intent alone suffices.

### ExclusionClause

A clause excluding identity exploration, affirmation, or medically indicated care from conversion practice definitions. First-class entity because: (a) exclusion clauses are how organisations argue their practices are not prohibited; (b) multiple laws share the same boundary logic; (c) the boundary is itself contested.

```
id, parent_legal_definition (LegalDefinition._id),
excludes [exploration | affirmation | transition_care | mental_disorder_treatment | reflective_practice],
text_excerpt (verbatim from law),
interpretation_risks (how this exclusion has been or could be exploited),
used_in_policy_arguments (boolean + description)
```

**Seeded ExclusionClauses:**

| ID | Parent law | What is excluded | Interpretation risk |
|---|---|---|---|
| EC-MT-1 | Malta 2016 | Exploration/free development/affirmation via counselling; healthcare for gender identity affirmation | Pro-SOGICE actors argue "identity exploration" falls under this |
| EC-DE-1 | Germany 2020 | Treatment of medically recognised sexual preference disorders; medical procedures for gender identity | Used to argue clinical SOGICE targeting "disorders" is permitted |
| EC-BE-1 | Belgium 2023 | Help in healthcare regarding exploration/development; transition-related care | Same risk as Malta |
| EC-CA-1 | Canada C-4 | Identity exploration unless based on assumption one orientation is preferable | Strongest "for greater certainty" formulation — model clause |
| EC-FR-1 | France 2022 | Healthcare professional inviting reflection/prudence re: youth considering medical pathway | Allows clinicians to raise concerns — significant carve-out |
| EC-UK-MOU | UK MoU | "Pastoral support" exemption | Identified by Jayne Ozanne as primary loophole |

### Document Relationships

```
relationship_type: translates | responds_to | cites | is_cited_by | co-produced_with | funded_by_same_source | part_of_series
target_document_id: string
confidence: confirmed | probable | speculative
```

---

## PART VI — CONTROLLED VOCABULARY

### Cardinality Rules

| Category | Cardinality | Notes |
|---|---|---|
| Type | Exactly one | |
| Format | Exactly one | |
| Evidence | **One or more** | A document can be both Journalism and Testimony |
| Scope | Exactly one | Core / Contextual / Reference |
| Country | One or more | Organization's country, not document language |
| Tactic | One or more | Most important field |
| Actor | Zero or more | |
| Network | Zero or more | |
| Practice | Zero or more | Only if SOGICE explicitly present |
| Term | Zero or more | Promotional use only — never if critiqued |
| Harm | Zero or more | |
| Migration | Zero or more | |
| Function | Zero or more | |
| Landmark | Zero or more | |
| Flags | Zero or more | Pipeline control signals |

### Practice Tags (updated — ILGA-Europe aligned)

`Practice: Psychotherapy (change/suppression)` | `Practice: Spiritual Healing` | `Practice: Pastoral Care` | `Practice: Deliverance` | `Practice: Exorcism` | `Practice: Coaching/Counselling-Rebrand` | `Practice: Identity Realignment` | `Practice: Retreat / Bootcamp` | `Practice: Medicalization-Abuse` | `Practice: Hormonal-Intervention-Misuse` | `Practice: Family Pressure` | `Practice: Social Pressure / Community Pressure` | **`Practice: Physical-Coercion`** | **`Practice: Verbal-Abuse-Humiliation`**

**ILGA-Europe Intersections 2.0 alignment:**

| ILGA-Europe category | Maps to Practice tag |
|---|---|
| Family intervention | Practice: Family Pressure |
| Prayer/religious rituals/counselling | Practice: Spiritual Healing + Pastoral Care + Deliverance (split by intensity) |
| Psychological/psychiatric treatment | Practice: Psychotherapy (change/suppression) |
| Medication | Practice: Medicalization-Abuse + Hormonal-Intervention-Misuse |
| Physical/sexual violence | **Practice: Physical-Coercion** |
| Verbal abuse/humiliation | **Practice: Verbal-Abuse-Humiliation** |

### Function Tags

`Function: Slur` | `Function: Euphemism` | `Function: Conspiracy` | `Function: Pseudo-Diagnostic` | `Function: Identity-Policing` | `Function: Moral-Purity Frame` | `Function: Political Slogan` | `Function: Recruitment Frame` | `Function: Pastoral Rhetoric` | `Function: Disinformation Narrative` | `Function: Promotional Recruitment` | `Function: Testimonial Marketing`

### Flags (pipeline control)

`Flag: Historical Context` | `Flag: Testimony Extraction Required`

---

## PART VII — ASSET EXTRACTION PROTOCOL

| Asset type | Description | Target module(s) |
|---|---|---|
| prayer_script | Verbatim prayer text for or over LGBTQ+ person | JUST CHANGE, Retro Library |
| testimony_excerpt | First-person account of undergoing or surviving SOGICE | Testimony View, WebXR |
| conversion_script | Scripted dialogue for conducting SOGICE session | JUST CHANGE, Retro Library |
| course_structure | Formal program, module list, retreat schedule | Courses & Ministries Tracker |
| statistical_claim | Specific statistic supporting SOGICE claims | WebSearch Simulation, Historical Viz |
| network_connection | Explicit funding, co-production, or personnel link | Network Visualizer |
| terminology_coinage | Document appears to be first/early use of a term | Lexicon (first_documented_use) |
| visual_asset | Book cover, logo, film still, infographic | Retro Library, Video Library |
| legislative_quote | Statement in legislative context | Historical Visualizer |
| counter_sermon | Affirming theological statement usable critically | Trans Jesus Ministries Live |

Both Claude (primary) and ChatGPT (validation) extract assets. Each asset records `extracted_by: llm_primary | llm_validation | human`.

---

## PART VIII — MULTILINGUAL TERM MODEL

Canonical concept node with language variant children. Enables network graph to show one node while Lexicon exposes all variants with adoption timelines.

### Core Structure

```json
{
  "concept_id": "gender-ideology",
  "canonical_term": "Gender Ideology",
  "canonical_language": "en",
  "cluster": "Anti-Gender",
  "function": "Conspiracy",
  "first_documented_use": "1990s (Vatican theological documents)",
  "variants": [
    {"term": "kjønnsideologi", "language": "no", "tier": 2},
    {"term": "ideologia gender", "language": "it", "tier": 2},
    {"term": "idéologie du genre", "language": "fr", "tier": 2},
    {"term": "Gender-Ideologie", "language": "de", "tier": 2},
    {"term": "ideología de género", "language": "es", "tier": 2},
    {"term": "ideologia genderowa", "language": "pl", "tier": 2},
    {"term": "sukupuoli-ideologia", "language": "fi", "tier": 2},
    {"term": "genusideologi", "language": "sv", "tier": 2},
    {"term": "genderideológia", "language": "hu", "tier": 3}
  ]
}
```

```json
{
  "concept_id": "gender-dysphoria",
  "canonical_term": "Gender Dysphoria",
  "canonical_language": "en",
  "cluster": "Non-SOGICE",
  "function": "Clinical reference",
  "variants": [
    {"term": "kjønnsdysfori", "language": "no", "tier": 2},
    {"term": "disforia di genere", "language": "it", "tier": 2},
    {"term": "dysphorie de genre", "language": "fr", "tier": 2},
    {"term": "Geschlechtsdysphorie", "language": "de", "tier": 2},
    {"term": "disforia de género", "language": "es", "tier": 2},
    {"term": "dysforia płciowa", "language": "pl", "tier": 2},
    {"term": "sukupuolidysforia", "language": "fi", "tier": 2},
    {"term": "könsdysfori", "language": "sv", "tier": 2},
    {"term": "nemi diszfória", "language": "hu", "tier": 2},
    {"term": "δυσφορία φύλου", "language": "el", "tier": 2},
    {"term": "disforija tal-ġeneru", "language": "mt", "tier": 2}
  ]
}
```

### Attestation Tiers

| Tier | Definition | Examples |
|---|---|---|
| Tier-1 | Attested in legal or official policy text | Konversionsbehandlung (DE), prattiċi ta' konverżjoni (MT), Πρακτικές μεταστροφής (GR), konverteringsterapi (NO) |
| Tier-2 | Attested in high-quality NGO, academic, or advocacy sources | kjønnsideologi (NO), ideologia gender (IT) |
| Tier-3 | Inferred, machine-translated, or community-reported | Most Hungarian terms; some Finnish terms |

**Rule:** Tier-3 variants must not be used as tagging targets until confirmed by a corpus source. They may be listed as candidates with `attestation: "tier-3-needs-confirmation"`.

### Full Multilingual Quick Reference

| English | Norwegian | Italian | French | German | Spanish | Polish | Finnish | Swedish | Hungarian | Greek | Maltese |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Gender Ideology | kjønnsideologi | ideologia gender | idéologie du genre | Gender-Ideologie | ideología de género | ideologia genderowa | sukupuoli-ideologia | genusideologi | genderideológia | — | — |
| Gender Dysphoria | kjønnsdysfori | disforia di genere | dysphorie de genre | Geschlechtsdysphorie | disforia de género | dysforia płciowa | sukupuolidysforia | könsdysfori | nemi diszfória | δυσφορία φύλου | disforija tal-ġeneru |
| Reparative Therapy | reparativ terapi | terapie riparative | thérapies réparatrices | Reparativtherapie | terapia reparativa | terapia reparatywna | reparatiivinen terapia | reparativ terapi | reparatív terápia | — | — |
| Conversion Therapy | konverteringsterapi | terapie di conversione | thérapies de conversion | Konversionstherapie | terapia de conversión | terapia konwersyjna | konversioterapia | konverteringsterapi | konverziós terápia | θεραπείες μεταστροφής | — |
| Conversion Practices | konverteringspraksis | pratiche di conversione | pratiques de conversion | Konversionspraktiken | prácticas de conversión | praktyki konwersyjne | konversiokäytännöt | konverteringspraktiker | konverziós gyakorlatok | Πρακτικές μεταστροφής | prattiċi ta' konverżjoni |
| Pastoral Support | pastoral støtte | supporto pastorale | soutien pastoral | seelsorgerische Unterstützung | apoyo pastoral | wsparcie duszpasterskie | pastoraalinen tuki | pastoralt stöd | lelkipásztori támogatás | — | — |
| Watchful Waiting | avventende observasjon | osservazione attenta | observation vigilante | beobachtendes Abwarten | observación vigilante | czujne oczekiwanie | tarkkaileva odotus | avvaktande observation | figyelmes várakozás | — | — |
| Self-determination | selvbestemmelse | autodeterminazione | autodétermination | Selbstbestimmung | autodeterminación | samostanowienie | itsemääräämisoikeus | självbestämmande | önrendelkezés | — | — |
| Same-sex attraction | homofile tiltrekning | attrazione omosessuale | attrait homosexuel | gleichgeschlechtliche Anziehung | atracción homosexual | pociąg homoseksualny | — | samkönad attraktion | azonos nemű vonzalom | — | — |

### Linguistic Notes

**Finnish:** `sukupuolenkorjaus` (gender-affirming surgery) uses "correction/repair" — closer to "reparative" than English. Finnish signatories in lgbtihealth.eu manifesto (June 2025) opposing bans.

**Swedish:** `hen` (gender-neutral pronoun) is a target of Anti-Gender rhetoric in Scandinavian contexts.

**Hungarian:** Framing bans as restrictions on parental rights and counseling — documented Pro-Trans-SOGICE / Parental-Rights-Trans context.

**Eastern Orthodox:** "Criminalising prayer" argument has a distinct Orthodox variant. NIKI (Greece) frames EU ban proposals as "a methodical attempt to criminalize Orthodox spiritual life." Likely present in Serbian, Romanian, and Russian Orthodox materials.

**French:** `morinom` (deadname) relevant to Anti-Trans harm documentation.

**Russian:** `goluboy` / `rozovy` (coded color terms) relevant to migration/asylum cluster.

**Spanish legal:** Ley 4/2023 Art. 17 uses `métodos, programas y terapias de aversión, conversión o contracondicionamiento` — the modality terms **aversión** and **contracondicionamiento** are detection markers for historical SOGICE documents in Spanish.

---

## PART IX — VISIBILITY AND CONTENT CONTROL

Three-tier model: `public` | `research_contextualized` | `internal_only`

**Slur display protocol:** All Lexicon entries tagged Function: Slur must include: (1) the term, (2) function tag, (3) harm level, (4) SOGICE usage note, (5) source attribution, (6) framing statement: "This term appears in the SurvivingSOGICE corpus in the context of [X]. It is documented here for research completeness."

**Troll/paraphilic terms:** Mandatory `origin_note` field. "This term was created by anti-LGBTQ+ actors to falsely associate LGBTQ+ identities with [X]." Tag: Anti-LGBT Conspiracy + Groomer-Panic.

---

## PART X — BOUNDARY CONCEPTS

### AffirmativeOrExploratorySupport

The family of therapeutic, pastoral, and counselling approaches that support a person in exploring and/or affirming their SOGIE without a predetermined outcome. Explicitly excluded from conversion practice definitions in Malta, Belgium, Canada, Germany, and Wales.

Critically important as a **boundary concept**: the same language ("identity exploration," "exploratory therapy") is used both by legitimate affirming practitioners and by pro-SOGICE actors claiming their practice is "merely exploratory." The difference is whether the practice presupposes that LGBTQ+ identity is undesirable, changeable, or in need of correction.

**Tagging rule:** When a source invokes "exploration" or "affirmation" language:
- `stance_profile: legal_administrative` if a legal document defining the exclusion
- `stance_profile: critical_advocacy` if an anti-SOGICE document
- `stance_profile: promotional` + Cluster: Policy-Resistance if a pro-SOGICE document invoking this language to claim their practice is excluded from bans

The last case (pro-SOGICE invoking exploration language to claim exemption) is one of the most important detection targets in the corpus.

---

## PART XI — OPEN QUESTIONS

| # | Question | Affects | Status |
|---|---|---|---|
| Q5 | Norwegian org rebranding histories (Foreldrenettverket, HBRS) | Entity registry | Open — populate from corpus |
| Q10 | "Oxygen-Gideon" — not found on iftcc.org/operation-gideon; requires direct corpus validation | Lexicon | Open |
| Q13 | France ExclusionClause (clinicians raising concerns about youth transition) — legitimate caution or SOGICE-adjacent carve-out? | ExclusionClause model | Open |
| Q14 | JUST CHANGE™ integration with i-Doc shell — iframe, deep link, or standalone? | Platform architecture | Open |

**Resolved:**
- Q7: Blanchard typology documents → classify as Anti-Trans (C5)
- Q11: France harm threshold → yes, model as separate legal variant
- Q12: Physical-Coercion + Verbal-Abuse-Humiliation → added to Practice vocabulary
- Q9: Finnish sukupuolenkorjaus → will resolve through corpus ingestion

---

## PART XII — WHAT THIS DOCUMENT IS NOT

Not the Sanity schema. Not the LLM system prompt. Not the Lexicon (term definitions in SOGICE_Lexicon_v2.0.md). Not the tagging guide for the intern. Not the PRD.

---

*SOGICE Discourse Ontology v3.0 · April 2026 · SurvivingSOGICE PhD Research Archive · University of Bergen*
*Companion documents: SOGICE_Lexicon_v2.0.md · PRD_v3.1.md · Entity_Registry_v1.1.md · TAGGING_GUIDE.md*
