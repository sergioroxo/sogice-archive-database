# SurvivingSOGICE — Entity Registry v1.1
**University of Bergen · SurvivingSOGICE PhD Research Archive**
*Phase 0 Document · Seed list — expand as corpus ingestion reveals new entities*
*Companion: SOGICE_Ontology_v3.0.md · PRD_v3.1.md*

---

## What this document is

The seed entity registry for the SurvivingSOGICE platform. Every named actor, person, law, and event known from the existing vocabulary and corpus, structured as typed entities ready for import into Sanity and Supabase.

**Four entity types:** Person / Organization / Law-Policy / Event

**Workflow status:** All entries here are `seeded` — they exist in this registry but have not yet been verified against source documents. When a document in the corpus confirms an entity, update status to `confirmed` and add `source_documents` references.

**Versioning note:** This is the seed list. The registry grows with every ingestion batch as NER surfaces new entities and researchers confirm them. The `former_names` field handles organizational rebranding. The `contested_figure` boolean handles researchers cited by both sides.

---

## PART 1 — ORGANIZATIONS

### Pro-SOGICE Organizations

---

**IFTCC** (International Federation for Therapeutic and Counselling Choice)
- Type: pseudo-professional-body / advocacy
- Country: UK (internationally operating)
- Founded: ~2015
- Former names: None known
- Description: Principal international pro-SOGICE advocacy and pseudo-professional body. Frames conversion therapy as legitimate therapeutic specialization. Coordinates Operation Gideon campaign (2025–). Key legislative lobbying organization in UK, Scottish, and EU contexts.
- Visibility: public
- Status: seeded

**NARTH** (National Association for the Research and Therapy of Homosexuality)
- Type: pseudo-professional-body / therapy_practice
- Country: USA
- Former names: [{name: "Alliance for Therapeutic Choice and Scientific Integrity", from_year: 2014}]
- Description: Major US pro-SOGICE pseudo-professional organization founded by Joseph Nicolosi. Rebranded to Alliance for Therapeutic Choice in 2014. Produced reparative therapy framework and pseudo-scientific literature on sexual orientation change.
- Note: Contested — some former members now oppose SOGICE
- Status: seeded

**Core Issues Trust**
- Type: advocacy / ministry
- Country: UK
- Founded: ~2010
- Description: UK pro-SOGICE advocacy organization. Co-produced X-Out-Loud testimonial platform. Produced major legislative submissions opposing UK conversion therapy bans. Associated with Mike Davidson.
- Status: seeded

**X-Out-Loud**
- Type: media / advocacy
- Country: UK / Malta (multinational)
- Description: Testimonial platform for "formerly LGBT" people promoted by IFTCC and Core Issues Trust. Recommended to Scottish government consultation as evidence for SOGICE. Matthew Grech (Malta) is a prominent contributor.
- Parent organization: IFTCC / Core Issues Trust
- Status: seeded

**Courage International**
- Type: ministry
- Country: USA (internationally operating)
- Founded: 1980
- Description: Catholic SOGICE ministry offering "pastoral support" for gay Catholics through the celibacy pathway. Operates EnCourage (support for families). Uses "living chastely" terminology. Active across English-speaking Catholic contexts and internationally.
- Status: seeded

**Restored Hope Network**
- Type: ministry / network_node
- Country: USA
- Description: US evangelical ex-gay ministry network founded in 2012 after Exodus International's dissolution. Coordinates multiple member ministries.
- Status: seeded

**Brothers Road**
- Type: ministry
- Country: USA
- Description: US SSA-focused ministry for men. Uses SSA terminology extensively.
- Status: seeded

**Homosexuals Anonymous**
- Type: ministry
- Country: USA
- Description: 12-step-modeled conversion ministry. One of the oldest ex-gay organizations.
- Status: seeded

**Alliance for Therapeutic Choice and Scientific Integrity**
- Type: pseudo-professional-body
- Country: USA
- Former names: [{name: "NARTH", from_year: 1992, to_year: 2014}]
- Description: Rebranded NARTH. Continues production of pseudo-scientific SOGICE literature.
- Status: seeded

**American College of Pediatricians**
- Type: pseudo-professional-body
- Country: USA
- Founded: 2002
- Description: SOGICE-affiliated organization that deliberately mimics the American Academy of Pediatrics to simulate clinical consensus. Produces anti-LGBTQ+ and anti-gender-affirming care statements. A False-Scientific-Authority marker — when cited, check whether it is confused with the legitimate AAP.
- Status: seeded

**SEGM** (Society for Evidence-Based Gender Medicine)
- Type: pseudo-professional-body
- Country: USA / internationally operating
- Description: Anti-trans pseudo-academic body. Claims to represent evidence-based clinical practice; in practice coordinates opposition to gender-affirming care. Connected to Genspect network.
- Status: seeded

**Genspect**
- Type: advocacy / pseudo-professional-body
- Country: International
- Description: International anti-trans organization claiming to offer "exploratory" therapy. Explicitly invokes anti-SOGICE language while promoting SOGICE-equivalent restrictions on trans youth. Pro-Trans-SOGICE cluster.
- Status: seeded

**4thWaveNow**
- Type: advocacy
- Country: USA
- Description: Parental-rights anti-trans organization. Uses harm-of-SOGICE language against gender-affirming care for trans youth. Pro-Trans-SOGICE cluster.
- Status: seeded

**Parents of ROGD Kids**
- Type: advocacy
- Country: USA / International
- Description: Parental network using ROGD framework to oppose gender-affirming care for their children. Feeds the ROGD narrative with testimonial material.
- Status: seeded

**Family Research Council (FRC)**
- Type: advocacy
- Country: USA
- Description: Major US religious-right advocacy organization. Produces anti-LGBTQ+ policy materials. Connected to Focus on the Family and World Congress of Families networks.
- Status: seeded

**Focus on the Family**
- Type: advocacy / media
- Country: USA
- Description: Major US evangelical media and advocacy organization. Foundational to anti-LGBTQ+ conservative movement. Produced YAB (young adults brochures) in the corpus.
- Status: seeded

**Family Watch International**
- Type: advocacy / network_node
- Country: USA
- Description: US organization coordinating international conservative family policy advocacy. Anti-LGBTQ+ and pro-SOGICE internationally.
- Status: seeded

**World Congress of Families**
- Type: network_node
- Country: USA (internationally operating)
- Description: Major transnational anti-LGBTQ+ network coordinating US, European, and Russian conservative family organizations. Connected to National Organization of Marriage and Russian Orthodox networks.
- Status: seeded

**MassResistance**
- Type: advocacy
- Country: USA
- Description: US anti-LGBTQ+ organization. Produces aggressive anti-LGBTQ+ advocacy materials.
- Status: seeded

**PFOX** (Parents and Friends of Ex-Gays and Gays)
- Type: advocacy
- Country: USA
- Description: US organization promoting ex-gay identity. Has attempted to place materials in school sex education curricula.
- Status: seeded

**Exodus International** (legacy/dissolved)
- Type: ministry / network_node
- Country: USA
- Founded: 1976
- Dissolved: 2013
- Description: The foundational ex-gay ministry network. Dissolved in 2013 when president Alan Chambers apologized for harm caused. Chapters and successor organizations persist globally under various names.
- Note: Exodus Global Alliance (international chapter) continues separately.
- Status: seeded

**Exodus Global Alliance**
- Type: network_node / ministry
- Country: International
- Description: International affiliate network descended from Exodus International. Continues operating in contexts where Exodus International dissolved.
- Status: seeded

**Bethel Church / Bethel Sozo**
- Type: ministry / church
- Country: USA (Redding CA)
- Description: Charismatic megachurch. Bethel Sozo is their inner-healing ministry that has been used for SOGICE practices including deliverance from homosexuality.
- Status: seeded

**Free to Change**
- Type: advocacy / ministry
- Country: International
- Description: Pro-SOGICE advocacy organization. Produced research and promotional materials in the corpus.
- Status: seeded

**Person and Identity Project (PIP)**
- Type: advocacy / ministry
- Country: USA / UK
- Description: Evangelical organization producing SOGICE-supporting materials including school FAQs and pastoral papers. Frequently cited in the corpus (PIP-FAQs-Download.pdf).
- Status: seeded

**Center for Faith, Sexuality and Gender**
- Type: advocacy / ministry
- Country: USA
- Description: Evangelical organization producing "pastoral papers" on same-sex attraction and gender identity. Side B theology. Associated with Preston Sprinkle. Produces materials used in evangelical pastoral training.
- Status: seeded

---

### Anti-SOGICE Organizations

---

**ILGA-Europe**
- Type: advocacy
- Country: EU (Brussels)
- Description: International Lesbian, Gay, Bisexual, Trans and Intersex Association — European branch. Principal LGBTQ+ rights advocacy body at EU level. Produces annual reviews tracking LGBTQ+ rights progress.
- Status: seeded

**GPAHE** (Global Project Against Hate and Extremism)
- Type: advocacy / monitoring
- Country: USA
- Description: Anti-extremism monitoring organization. Produced key ecosystem reports on online conversion therapy that are in the Anti-SOGICE corpus.
- Status: seeded

**GATE** (Global Action for Trans Equality)
- Type: advocacy
- Country: International
- Description: Transnational trans-rights advocacy organization. Produces reports on conversion therapy practices globally.
- Status: seeded

**Stonewall**
- Type: advocacy
- Country: UK
- Description: UK LGBTQ+ rights organization. Produces policy advocacy materials and conversion therapy campaign resources.
- Status: seeded

**Bufdir** (Norwegian Directorate for Children, Youth and Family Affairs)
- Type: government
- Country: Norway
- Description: Norwegian state body. Produced materials on LGBTQ+ rights and conversion therapy relevant to the Norwegian legislative process.
- Status: seeded

**NCLR** (National Center for Lesbian Rights)
- Type: advocacy / legal
- Country: USA
- Description: US legal advocacy organization for LGBTQ+ rights. Has led litigation on conversion therapy bans.
- Status: seeded

**Southern Poverty Law Center (SPLC)**
- Type: advocacy / monitoring
- Country: USA
- Description: Monitors extremism and hate groups in the USA. Has designated several pro-SOGICE organizations as hate groups. Produced conversion therapy corpus reports.
- Status: seeded

**IRCT** (International Rehabilitation Council for Torture Victims)
- Type: advocacy / human rights
- Country: International (Denmark-based)
- Description: Frames conversion therapy as torture under international human rights law. Relevant to the legal and policy cluster.
- Status: seeded

**Ozanne Foundation**
- Type: advocacy
- Country: UK
- Description: LGBTQ+ Christian advocacy organization. Jayne Ozanne is founder and prominent UK survivor advocate who has specifically documented pastoral loopholes in conversion therapy ban proposals.
- Status: seeded

**Galop**
- Type: advocacy / victim support
- Country: UK
- Description: UK LGBTQ+ victim support organization. Produced survivor testimony and harm documentation.
- Status: seeded

---

### European Pro-SOGICE Organizations

---

**Ordo Iuris**
- Type: advocacy / legal
- Country: Poland
- Description: Polish Catholic legal advocacy organization. Prominent in European anti-gender movement. Produces policy materials opposing LGBTQ+ rights and conversion therapy bans.
- Status: seeded

**SPCh** (Association of Christian Psychologists, Poland)
- Type: pseudo-professional-body / advocacy
- Country: Poland
- Founded: ~2020
- Description: Polish association of Christian psychologists. Produced 2024 Standards and Guidelines for diagnosis and therapy of children and adolescents with gender identity issues. Contains IFTCC principles references. Key document in the corpus.
- Status: seeded

**Wüstenstrom**
- Type: ministry
- Country: Germany / Switzerland
- Description: German-language ex-gay ministry. Produces SSA-Rhetoric and Pastoral-Coercion materials in German. Key actor for the German-speaking corpus.
- Status: seeded

**Hazte Oír / CitizenGO**
- Type: advocacy / network_node
- Country: Spain (internationally operating)
- Description: Spanish conservative advocacy organization with international CitizenGO petition platform. Active in anti-gender movement across multiple countries. Connected to World Congress of Families and European anti-gender networks.
- Status: seeded

**Torrents de Vie**
- Type: ministry
- Country: France
- Description: French Catholic ex-gay ministry. Key actor for French-language SSA-Rhetoric and Pastoral-Coercion corpus.
- Status: seeded

**Onderweg.nu**
- Type: ministry
- Country: Netherlands
- Description: Dutch ex-gay ministry. Key actor for Dutch-language corpus.
- Status: seeded

**HUG / Knus**
- Type: ministry
- Country: Denmark
- Description: Danish ex-gay ministry. Key actor for Danish-language corpus.
- Status: seeded

**AdamogEva.dk**
- Type: ministry / media
- Country: Denmark
- Description: Danish conservative Christian organization promoting heterosexual marriage. Relevant to SSA-Rhetoric and Pastoral-Coercion clusters.
- Status: seeded

**Terapia do Amor / Igreja Universal**
- Type: ministry / church
- Country: Portugal / Brazil
- Description: Portuguese and Brazilian conversion therapy context. Igreja Universal is a Pentecostal megachurch with documented conversion therapy practices.
- Status: seeded

**Coram Fratribus**
- Type: media / blog
- Country: Norway
- Description: Norwegian conservative Catholic blog. Relevant to Norwegian Anti-Gender and Pastoral-Coercion corpus.
- Status: seeded

**Foreldrenettverket**
- Type: advocacy
- Country: Norway
- Description: Norwegian parents network. Relevant to Norwegian legislative context and parental rights framing.
- Note: Q6 — rebranding history unknown; populate from corpus.
- Status: seeded

**HBRS** (Norwegian detrans organization)
- Type: advocacy
- Country: Norway
- Description: Norwegian detransitioner organization. Relevant to Anti-Trans/ROGD and Pro-Trans-SOGICE clusters in Norwegian context.
- Note: Q6 — rebranding history unknown; populate from corpus.
- Status: seeded

**Not the Same Love / Notthesamelove.com**
- Type: ministry / media
- Country: Indonesia (with Italy connection)
- Description: Indonesian pro-SOGICE ministry. Also appears in Italian context. Sihol Gianito Situmorang is a named actor.
- Status: seeded

**Transformed Life Community (TLC Indonesia)**
- Type: ministry
- Country: Indonesia
- Description: Indonesian conversion therapy ministry. Key actor for Indonesian corpus.
- Status: seeded

**LGB Alliance (UK)**
- Type: advocacy
- Country: UK
- Founded: 2019
- Description: UK organization explicitly founded to separate LGB from T. Opposes trans inclusion in gay/lesbian spaces and rights. Pro-Trans-SOGICE cluster: explicitly opposes conversion therapy for gay people while advocating SOGICE-equivalent restrictions for trans people.
- Status: seeded

**LGB Alliance Australia**
- Type: advocacy
- Country: Australia
- Description: Australian affiliate of LGB Alliance (UK). Same structural position in Pro-Trans-SOGICE cluster.
- Status: seeded

**Fair Play for Women**
- Type: advocacy
- Country: UK
- Description: UK gender-critical organization. Opposes trans women in women's sports and spaces. Anti-Trans/ROGD cluster.
- Status: seeded

**For Women Scotland**
- Type: advocacy
- Country: UK (Scotland)
- Description: Scottish gender-critical organization. Active in Scottish legislative debates including conversion therapy ban consultation.
- Status: seeded

**Sex Matters**
- Type: advocacy
- Country: UK
- Description: UK organization promoting "sex-based rights." Anti-Trans/ROGD cluster.
- Status: seeded

**Transgender Trend**
- Type: advocacy / media
- Country: UK
- Description: UK anti-trans organization. Produces materials opposing gender-affirming care for youth. Pro-Trans-SOGICE cluster.
- Status: seeded

**Athena Forum**
- Type: advocacy
- Country: EU
- Description: EU-based organization opposing "conversion practices" framing. Explicitly contests the term "conversion practices" to argue it conflates harmful practices with legitimate religious counseling. Policy-Resistance cluster.
- Status: seeded

**NIKH / NIKI**
- Type: political party
- Country: Greece
- Founded: ~2023
- Description: Greek conservative/nationalist political party. Has framed EU conversion therapy ban proposals as "a methodical attempt to criminalize Orthodox spiritual life." Represents the Eastern Orthodox variant of the PastoralCoercion-LegislativeLoophole tactic.
- Source: NIKI party statement, February 2026
- Status: seeded

---

## PART 2 — PERSONS

---

**Matthew Grech**
- Role: influencer (ex-gay)
- Country: Malta
- Affiliated organizations: X-Out-Loud, IFTCC
- Public profile: true
- Contested figure: false
- Description: Maltese ex-gay influencer. Prominent in IFTCC and X-Out-Loud networks. Acquitted under Malta's 2016 conversion therapy ban in March 2026 — a landmark legal development for anti-ban advocates.
- Status: seeded

**Victor Novitchi**
- Role: influencer (ex-gay)
- Country: Romania / Moldova
- Public profile: true
- Description: Romanian/Moldovan ex-gay influencer. Active in Eastern European SOGICE networks.
- Status: seeded

**Pedro Choy**
- Role: therapist (acupuncture conversion)
- Country: Portugal
- Public profile: true
- Description: Portuguese practitioner of acupuncture-based conversion practices. Relevant to Portuguese corpus.
- Status: seeded

**Dr. Sybrand de Vaal**
- Role: therapist
- Country: South Africa
- Public profile: true
- Description: South African pro-SOGICE therapist. Relevant to South African and international corpus.
- Status: seeded

**Joseph Nicolosi†**
- Role: founder / therapist
- Country: USA
- Affiliated organizations: NARTH
- Contested figure: false
- Description: Founder of NARTH and originator of reparative therapy. Died 2017. His framework (Father Wound, Reparative Drive, Reparative Therapy) remains foundational to contemporary SOGICE pseudo-science.
- Status: seeded

**Kenneth Zucker**
- Role: researcher
- Country: Canada
- Affiliated organizations: CAMH (Centre for Addiction and Mental Health, Toronto) — former
- Contested figure: TRUE
- Contested figure note: Zucker's work is cited by both pro- and anti-SOGICE actors. He was dismissed from CAMH in 2015 under disputed circumstances following an external review. Some trans advocates consider his "watchful waiting" approach SOGICE-equivalent for trans youth; others defend his research as legitimate. His own position is that he neither opposes nor promotes conversion therapy.
- Status: seeded

**Robert Spitzer†**
- Role: researcher
- Country: USA
- Contested figure: TRUE
- Contested figure note: Spitzer's 2001 study claiming some homosexuals can change their orientation through therapy generated major controversy. He subsequently retracted the study in 2012 and issued a public apology to gay men and lesbians. His work is still cited by pro-SOGICE actors despite the retraction. Died 2015.
- Status: seeded

**Lisa Littman**
- Role: researcher
- Country: USA
- Affiliated organizations: Brown University (former)
- Contested figure: TRUE
- Contested figure note: Proposed ROGD hypothesis in 2018. The paper's methodology was widely criticized — it surveyed parents from anti-trans websites rather than the young people themselves. The journal issued a correction. Brown University initially removed the press release about the paper before later restoring it. Littman maintains her research is legitimate. Her work is foundational to anti-trans SOGICE rhetoric.
- Status: seeded

**Ray Blanchard**
- Role: researcher
- Country: Canada
- Affiliated organizations: CAMH (former)
- Contested figure: TRUE
- Contested figure note: Proposed the two-type typology (HSTS/AGP) for trans women and coined "autogynephilia." His framework is rejected by mainstream trans medicine and by trans people broadly, but continues to be used by anti-trans actors including SEGM and gender-critical advocates.
- Status: seeded

**Jayne Ozanne**
- Role: survivor / advocate
- Country: UK
- Affiliated organizations: Ozanne Foundation
- Contested figure: false
- Public profile: true
- Description: UK conversion therapy survivor and leading anti-SOGICE advocate. Has specifically documented the pastoral loophole in UK conversion therapy ban proposals. Christian; her work is significant because she argues from within a faith perspective.
- Status: seeded

**Víctor Madrigal-Borloz**
- Role: researcher / human rights official
- Country: Costa Rica (internationally)
- Affiliated organizations: UN Independent Expert on protection against violence and discrimination based on SOGI (2018–2023)
- Description: Produced key UN Human Rights Council reports on conversion therapy practices globally. Primary UN-level source for anti-SOGICE framing.
- Status: seeded

**Anne Kalvig**
- Role: researcher (contested)
- Country: Norway
- Affiliated organizations: University of Stavanger
- Contested figure: TRUE
- Contested figure note: Norwegian researcher who has written about gender-critical perspectives. Her work is cited in Norwegian anti-gender and anti-trans contexts while she identifies as a feminist academic. Position on SOGICE specifically is unclear; flag for corpus investigation.
- Status: seeded

**Richard Beharie**
- Role: influencer (ex-gay)
- Country: Norway
- Public profile: true
- Description: Norwegian ex-gay influencer. Relevant to Norwegian corpus.
- Status: seeded

**Sihol Gianito Situmorang**
- Role: founder / minister
- Country: Indonesia
- Affiliated organizations: Not the Same Love / notthesamelove.com
- Public profile: true
- Description: Founder of Indonesian pro-SOGICE platform.
- Status: seeded

**Fr. Jakob Rolland**
- Role: pastor
- Country: Iceland
- Affiliated organizations: Catholic Church (Iceland)
- Description: Catholic priest investigated under Iceland's conversion therapy ban in March 2026 following public statements. Cited by Operation Gideon campaign as evidence of ban overreach.
- Source: European Conservative, March 2026
- Status: seeded

---

## PART 3 — LAWS AND POLICIES

---

**Malta Affirmation of Sexual Orientation, Gender Identity and Gender Expression Act (2016)**
- Country: Malta
- Year: 2016
- Status: enacted
- Applies to: ban (conversion therapy ban — first in Europe)
- Description: First European conversion therapy ban. Prohibited practices aiming to change, repress, or eliminate a person's sexual orientation, gender identity, or gender expression. Subject to a significant test case in 2026 (Matthew Grech acquittal).
- Historical significance: high
- Status: seeded

**California SB 1172 (2012)**
- Country: USA
- Year: 2012
- Status: enacted (first US state ban on conversion therapy for minors)
- Applies to: ban
- Description: First US state law prohibiting licensed mental health providers from performing conversion therapy on minors. Upheld by US Supreme Court (2014 — declined to review).
- Historical significance: high
- Status: seeded

**France conversion therapy ban (2022)**
- Country: France
- Year: 2022
- Status: enacted
- Applies to: ban
- Status: seeded

**Belgium conversion therapy ban (2023)**
- Country: Belgium
- Year: 2023
- Status: enacted
- Applies to: ban
- Description: Criminalizes physical intervention or psychological conditioning to modify or suppress sexual orientation, gender identity, or gender expression. Legal terminology includes pressions psychiques (psychological pressures) and conditionnement psychologique.
- Status: seeded

**Cyprus conversion therapy ban (2023)**
- Country: Cyprus
- Year: 2023
- Status: enacted
- Applies to: ban
- Status: seeded

**Portugal conversion therapy ban (2024)**
- Country: Portugal
- Year: 2024
- Status: enacted
- Applies to: ban
- Status: seeded

**Spain Ley Trans / Ley 4/2023 (2023)**
- Country: Spain
- Year: 2023
- Status: enacted
- Applies to: ban + trans rights protection
- Description: Spanish law for the real and effective equality of trans people and to guarantee the rights of LGBTI persons. Includes conversion therapy prohibition alongside broader trans rights provisions.
- Status: seeded

**Norway conversion therapy hearing process (2022)**
- Country: Norway
- Year: 2022
- Status: legislative process (outcome: pending)
- Applies to: ban (under consideration)
- Description: Norwegian parliamentary consultation on conversion therapy prohibition. Generated significant corpus material including submissions from Christian organizations and secular opponents. Key corpus: 04-Legal & Policy > 04.5.1 — Norwegian Hearing Process 2022 collections.
- Historical significance: high (Norwegian context)
- Status: seeded

**Victoria (Australia) conversion therapy ban**
- Country: Australia (Victoria)
- Status: enacted
- Applies to: ban (criminal)
- Description: Described in the corpus as "best practice" for the criminal ban model. Explicitly criminal rather than regulatory, which distinguishes it from most other bans.
- Historical significance: high
- Status: seeded

**Scotland conversion therapy consultation (PE1817)**
- Country: UK (Scotland)
- Status: legislative process (ongoing)
- Applies to: ban (under consideration)
- Description: Scottish Parliament petition PE1817 "End Conversion Therapy." Generated corpus submissions from pro-SOGICE organizations including IFTCC. Scottish church leaders claimed ban would "criminalise" Christian beliefs (October 2025).
- Status: seeded

**UK Memorandum of Understanding on conversion therapy (multiple versions)**
- Country: UK
- Status: policy instrument (not legislation)
- Applies to: ban (voluntary professional code)
- Description: Non-legislative agreement between UK professional bodies against conversion therapy. Has been criticized by survivors including Jayne Ozanne for its pastoral support exemption, identified as the primary legislative loophole.
- Status: seeded

**Iceland conversion therapy ban (2023)**
- Country: Iceland
- Year: 2023
- Status: enacted
- Applies to: ban
- Description: Led to investigation of Catholic priest Fr. Jakob Rolland in March 2026 — first reported investigation under Icelandic ban. Cited by Operation Gideon campaign.
- Status: seeded

---

## PART 4 — EVENTS

---

**Exodus International dissolution (2013)**
- Type: organizational_founding (dissolution)
- Date: June 2013
- Actors: Exodus International; Alan Chambers (president)
- Description: The foundational ex-gay ministry network dissolved after president Alan Chambers issued a public apology to gay men and lesbians, acknowledging harm caused. Marks a major inflection point in SOGICE history. Successor organizations immediately formed (Restored Hope Network, Exodus Global Alliance continues internationally).
- Historical significance: high
- Status: seeded

**APA Task Force Report on Appropriate Therapeutic Responses to Sexual Orientation (2009)**
- Type: academic_publication / policy
- Date: August 2009
- Actors: American Psychological Association
- Description: Foundational anti-SOGICE policy document by the APA. Reviewed the scientific evidence on SOCE; concluded there was insufficient evidence of efficacy and significant evidence of harm. Led to the APA resolution against conversion therapy for minors. Core corpus document.
- Historical significance: high
- Status: seeded

**Core Issues Trust v Transport for London (2014)**
- Type: court_case
- Date: 2014
- Actors: Core Issues Trust; Transport for London
- Description: UK court case involving pro-gay-conversion advertising on London buses. Core Issues Trust argued that discrimination against "formerly LGBT" people is unlawful under the Equality Act. Cited by pro-SOGICE actors as establishing "former LGBT" as a protected characteristic.
- Historical significance: medium
- Status: seeded

**Operation Gideon launch (2025)**
- Type: network_formation / legislative_campaign
- Date: June 2025
- Actors: IFTCC (coordinator); affiliated organizations TBC
- Description: IFTCC launched coordinated pan-European campaign to challenge conversion therapy bans across EU member states. See entity in Organizations section.
- Historical significance: high (current)
- Status: seeded

**IFTCC International Declaration on Therapeutic and Pastoral Choice (2024)**
- Type: publication / declaration
- Date: February 2024
- Actors: IFTCC; Christian Medical & Dental Associations
- Description: Transnational statement asserting right to therapeutic and pastoral choice. Functions as a coordination document across national SOGICE networks. Signatories to be identified from corpus.
- Historical significance: medium
- Status: seeded

**Matthew Grech acquittal, Malta (2026)**
- Type: court_case
- Date: March 2026
- Actors: Matthew Grech; Maltese courts
- Description: First significant legal challenge to an EU conversion therapy ban resulting in acquittal. Cited as precedent by anti-ban advocates claiming pastoral speech is protected under Malta's 2016 law.
- Historical significance: high (ongoing significance for European legal landscape)
- Status: seeded

**Fr. Jakob Rolland investigation, Iceland (2026)**
- Type: investigation
- Date: March 2026
- Actors: Fr. Jakob Rolland; Icelandic authorities
- Description: Catholic priest investigated under Iceland's 2023 conversion therapy ban following public statements. Cited by Operation Gideon as evidence that bans criminalize religious speech.
- Historical significance: medium
- Status: seeded

**Spitzer retraction and apology (2012)**
- Type: academic_publication (retraction)
- Date: 2012
- Actors: Robert Spitzer
- Description: Spitzer published a letter in Archives of Sexual Behavior retracting his 2001 study claiming some homosexuals can change orientation through therapy, and issued a public apology. Significant inflection point in pseudo-scientific SOGICE legitimacy.
- Historical significance: high
- Status: seeded

**ICD-11 adoption (2019)**
- Type: policy
- Date: May 2019
- Actors: World Health Organization (WHO); World Health Assembly
- Description: WHO adopted ICD-11, which moved trans-related diagnoses out of the mental disorders chapter. Significantly changed the landscape for anti-trans SOGICE arguments that relied on ICD-10 pathologization of trans identity.
- Historical significance: high
- Status: seeded

**Detransition Awareness Day (annual, March 12)**
- Type: media_event (recurring)
- Actors: Anti-trans advocacy organizations
- Description: Annual organized anti-trans event using detransitioner narratives to argue against gender-affirming care. Feeds the Detrans Pandemic narrative.
- Historical significance: medium
- Status: seeded

---

## APPENDIX — Entities Under Investigation

The following entities appeared in the corpus but need further verification before being added to the main registry:

| Entity | Type | Why flagged | Action needed |
|--------|------|-------------|---------------|
| Genid Norge | Organization (Norway) | Appears in vocabulary — Norwegian gender-related organization | Confirm pro- or anti-SOGICE position |
| Subjekt | Media (Norway) | Norwegian media organization | Confirm role in corpus |
| Gay Men's Network (UK) | Organization | Appears in vocabulary | Confirm position |
| Lesbian Project (UK) | Organization | Gender-critical adjacent | Confirm role |
| Battle of Ideas / Academy of Ideas (UK) | Media/advocacy | UK libertarian organization | Confirm role in SOGICE discourse |
| Liberty Counsel (USA) | Legal/advocacy | US religious liberty legal organization | Confirm role in SOGICE legislative submissions |
| Family Policy Alliance (USA) | Advocacy | US state-level policy advocacy | Confirm role |
| Relevant Christianity (Malta/USA) | Ministry | Malta/US connection | Confirm role |

---



---

## APPENDIX B — Additions from Validation Research (v1.1)

*Added April 2026 based on deep research validation of European legal landscape*

### New Laws

**Greece N.4931/2022, Article 62 — Πρακτικές μεταστροφής**
- Country: Greece
- Year in force: 2022
- Status: enacted
- Term: Πρακτικές μεταστροφής (Greek: "Conversion Practices")
- Description: Prohibits any treatment aiming to alter or suppress sexual orientation, gender identity, or gender expression. Applies to "vulnerable persons" (minors and adults under judicial support). Consent required for non-vulnerable adults. Bans advertising and promotion by professionals. Includes definitional clauses for gender identity/expression.
- Harm threshold: No
- Historical significance: high
- Status: seeded (v1.1)

**Norway §270 Criminal Code — konverteringsterapi**
- Country: Norway
- Year in force: 1 January 2024 (confirmed)
- Status: enacted
- Term: konverteringsterapi
- Description: Criminalises systematic methods intended to influence a person to change, deny, or suppress their sexual orientation or gender identity. Covers psychotherapeutic, medical, alternative, and religious methods. Adult offence requires "krenker" (violates) — intent alone insufficient. Minor provision: intent alone suffices (no "krenker" required). Includes marketing offence.
- Historical significance: high (directly relevant to Norwegian corpus)
- Note: Updates the earlier Norway hearing process entry — this is the enacted result.
- Status: seeded (v1.1)

**Council of Europe PACE Resolution 2643 (2026)**
- Jurisdiction: Council of Europe (pan-European, 46 member states)
- Year: 2026
- Status: resolution (not binding, but defines standard)
- Term: conversion practices
- Description: Parliamentary Assembly of the Council of Europe calls for bans on conversion practices across all member states. Defines conversion practices as "all measures or efforts aimed at changing, repressing, or suppressing" SOGIE. Most recent major pan-European definitional document.
- Historical significance: high
- Status: seeded (v1.1)

### New Events

**European Citizens' Initiative: Ban on Conversion Practices in the EU (2025)**
- Type: political_campaign / citizens_initiative
- Date: Submitted 17 November 2025
- Actors: EU civil society organizations
- Description: Formal EU petition submitted with 1,128,063 verified statements — above the 1 million threshold triggering a formal Commission response. Demands EU-wide legislative ban. Potential trigger for binding EU legislation.
- Historical significance: high
- Status: seeded (v1.1)

**ILGA-Europe Intersections 2.0 publication (January 2026)**
- Type: advocacy_report / measurement instrument
- Date: January 2026 (data from FRA LGBTIQ III survey, 2023)
- Actors: ILGA-Europe; EU Fundamental Rights Agency (underlying data)
- Description: First European prevalence measurement of conversion practices using a standardised survey instrument across EU member states. Practice taxonomy: family intervention, prayer/religious rituals/counselling, psychological/psychiatric treatment, medication, physical/sexual violence, verbal abuse/humiliation. Essential for aligning corpus tagging with European measurement categories.
- Historical significance: high (methodological significance for this research project)
- Status: seeded (v1.1)

**Council of Europe PACE Resolution 2643 adoption (2026)**
- Type: resolution
- Date: 2026
- Actors: Council of Europe Parliamentary Assembly
- Description: See LegalDefinition above.
- Historical significance: high
- Status: seeded (v1.1)

### Updated: Norway hearing process entry

The entry "Norway conversion therapy hearing process (2022)" in Part 3 should be understood as the legislative process that led to Norway §270 (above), which entered into force 1 January 2024. The hearing corpus (Zotero collection 04.5.1) documents the 2022 consultation that preceded this law.

---

*Entity Registry v1.1 · April 2026 · SurvivingSOGICE PhD Research Archive · University of Bergen*

