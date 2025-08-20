import streamlit as st
import PyPDF2
import re, json, math
from collections import Counter, defaultdict

st.set_page_config(page_title="RFP API Requirement Analyzer", page_icon="🧠", layout="wide")
st.title("🧠 RFP API Requirement Analyzer (6 Human-like Agents • Minimal Dependencies)")

# ---------- Utils ----------
def extract_text(pdf_file):
    try:
        reader = PyPDF2.PdfReader(pdf_file)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text
    except Exception as e:
        return ""

def split_sentences(text):
    # ultra-light sentence splitter
    s = re.sub(r"\s+", " ", text.strip())
    # keep abbreviations from over-splitting
    s = re.sub(r"(?<!\b[A-Z])\.(\s+)", ".<SPLIT>", s)
    parts = [p.strip() for p in re.split(r"<SPLIT>|[!?]\s+|\n", s) if p.strip()]
    return parts

STOPWORDS = set("""
a an the and or for to of in on with by from as at is are be been were was that this those these it its their them they we you your our us will shall may must should could can
if then else but not no nor such any all per each every etc via than into about over under between among within above below more less high low new current future past present
""".split())

# basic lemmatization-ish singularizer
def singularize(word):
    if word.endswith("ies"): return word[:-3] + "y"
    if word.endswith("ses"): return word[:-2]
    if word.endswith("s") and not word.endswith("ss"): return word[:-1]
    return word

def normalize_token(w):
    w = re.sub(r"[^a-z0-9\-]", "", w.lower())
    return singularize(w)

def top_keywords(text, k=30):
    toks = [normalize_token(w) for w in re.findall(r"[A-Za-z0-9\-]+", text)]
    toks = [t for t in toks if t and t not in STOPWORDS and len(t) > 2]
    counts = Counter(toks)
    return counts.most_common(k)

def find_snippets(sentences, terms, window=2, max_rows=5):
    out = []
    tset = {t.lower() for t in terms}
    for i, s in enumerate(sentences):
        s_low = s.lower()
        if any(t in s_low for t in tset):
            start = max(0, i - window)
            end = min(len(sentences), i + window + 1)
            chunk = " ".join(sentences[start:end])
            out.append(chunk)
            if len(out) >= max_rows:
                break
    return out

def score_presence(text, groups):
    # groups = {"label":[terms], ...}, returns dict label->(hits, evidence)
    tlow = text.lower()
    result = {}
    for label, terms in groups.items():
        hits = [t for t in terms if t.lower() in tlow]
        result[label] = (len(hits), hits)
    return result

def confidence_from_hits(hits, total_bins=5):
    # tiny non-linear mapping for human-like confidence
    return round(100 * (1 - math.exp(-hits / max(1, total_bins))), 1)

# ---------- Agent 1: Business Scope ----------
def agent_business_scope(text):
    sentences = split_sentences(text)
    scope_cues = [
        "objective", "objectives", "goal", "goals", "purpose", "scope",
        "outcome", "outcomes", "deliverable", "deliverables",
        "problem statement", "business need", "use case", "use cases", "requirements"
    ]
    snippets = find_snippets(sentences, scope_cues, window=0, max_rows=6)
    # pick top keywords as "themes"
    themes = [k for k,_ in top_keywords(" ".join(snippets), k=8)]
    rationale = "Looked for scope cues and summarized recurring nouns as themes."
    found = "Business Scope Found" if snippets else "Not Found"
    conf = confidence_from_hits(len(snippets))
    return {
        "label": found,
        "confidence": conf,
        "themes": themes,
        "evidence": snippets[:4],
        "rationale": rationale
    }

# ---------- Agent 2: Explicit API Mentions ----------
def agent_api_explicit(text):
    groups = {
        "explicit_api": ["api", "apis", "endpoint", "endpoints", "swagger", "openapi", "web service", "web services", "rest", "graphql", "soap"]
    }
    sc = score_presence(text, groups)
    hits = sc["explicit_api"][0]
    evidence_terms = sc["explicit_api"][1][:8]
    label = "API Explicitly Required" if hits > 0 else "Not Mentioned"
    conf = confidence_from_hits(hits)
    rationale = "Checked for direct mentions of APIs, endpoints, and standards keywords."
    return {"label": label, "confidence": conf, "hits": hits, "evidence_terms": evidence_terms, "rationale": rationale}

# ---------- Agent 3: Integration & Interoperability ----------
def agent_integration(text):
    groups = {
        "integration": [
            "integration", "integrate", "interoperability", "interface", "interface with",
            "connect", "connector", "data exchange", "synchronization", "sync",
            "third-party", "vendor", "partner", "ecosystem",
            "sap", "salesforce", "oracle", "workday", "servicenow", "netsuite", "dynamics"
        ]
    }
    sc = score_presence(text, groups)
    hits = sc["integration"][0]
    label = "Integration Needs (Likely API)" if hits > 0 else "No Integration Mention"
    conf = confidence_from_hits(hits)
    sentences = split_sentences(text)
    snippets = find_snippets(sentences, sc["integration"][1], window=1, max_rows=5)
    rationale = "Looked for verbs/nouns implying system-to-system connectivity and named platforms."
    return {"label": label, "confidence": conf, "hits": hits, "evidence": snippets[:4], "rationale": rationale}

# ---------- Agent 4: Compliance, Standards, Security ----------
def agent_compliance(text):
    groups = {
        "api_std": ["rest", "soap", "graphql", "openapi", "swagger", "json", "xml", "idempotency", "pagination", "rate limit", "throttle"],
        "security": ["oauth2", "oidc", "open id connect", "jwt", "m tls", "tls 1.2", "tls 1.3", "hmac", "api key", "scope", "consent"],
        "governance": ["iso 27001", "soc 2", "gdpr", "hipaa", "pci dss", "audit", "logging", "monitoring"]
    }
    sc = score_presence(text, groups)
    total_hits = sum(v[0] for v in sc.values())
    label = "API Standards / Security Requirements Found" if total_hits > 0 else "No Standards Mention"
    conf = confidence_from_hits(total_hits, total_bins=7)
    rationale = "Searched for standards (OpenAPI, REST), security (OAuth2/JWT/TLS), and compliance (ISO/SOC2/GDPR)."
    return {
        "label": label,
        "confidence": conf,
        "hits": {k:v[0] for k,v in sc.items()},
        "evidence_terms": {k:v[1][:6] for k,v in sc.items()},
        "rationale": rationale
    }

# ---------- Agent 5: Decision Summary (Voting & Reasoning) ----------
def agent_summary(a1, a2, a3, a4):
    # Weighted voting based on confidence and category
    votes = []
    if "Found" in a1["label"]: votes.append(("scope", a1["confidence"]))
    if "Explicitly" in a2["label"]: votes.append(("explicit", a2["confidence"]))
    if "Integration" in a3["label"]: votes.append(("integration", a3["confidence"]))
    if "Requirements Found" in a4["label"]: votes.append(("standards", a4["confidence"]))

    score = sum(c for _, c in votes)
    # thresholds chosen to feel "human"
    if score >= 120 or ("Explicitly" in a2["label"] and a2["confidence"] >= 50):
        decision = "✅ APIs are Required"
    elif score >= 60 or "Integration" in a3["label"]:
        decision = "🟡 APIs are Likely Required / Strongly Suggested"
    else:
        decision = "❌ APIs Not Clearly Required"

    rationale = "Combined explicit mentions, integration signals, and standards/security cues with weighted confidence."
    return {"decision": decision, "aggregate_score": round(score,1), "votes": votes, "rationale": rationale}

# ---------- Agent 6: Generate OpenAPI JSON (Dynamic, Heuristic) ----------
ENTERPRISE_CANON = [
    "customer", "order", "product", "catalog", "inventory", "supplier", "vendor", "invoice",
    "payment", "shipment", "employee", "user", "account", "ticket", "asset", "document",
    "contract", "case", "lead", "opportunity", "project", "task", "timesheet", "policy"
]

VERB_MAP = {
    "create": ["create", "add", "submit", "register", "ingest"],
    "read":   ["get", "retrieve", "list", "fetch", "query", "search", "view"],
    "update": ["update", "modify", "patch", "edit"],
    "delete": ["delete", "remove", "deprecate"]
}

def detect_resources_and_verbs(text):
    tlow = text.lower()

    # resources: from canon + capitalized domain nouns in text (simple heuristic)
    canon_hits = [r for r in ENTERPRISE_CANON if r in tlow or (r+"s") in tlow]
    caps = re.findall(r"\b([A-Z][a-zA-Z]{2,})\b", text)  # e.g., Customer, Invoice
    caps_norm = [normalize_token(c) for c in caps if c.lower() not in STOPWORDS]
    caps_counts = Counter([singularize(c) for c in caps_norm if len(c) >= 3])
    top_caps = [c for c, _ in caps_counts.most_common(10)]
    resources = list(dict.fromkeys(canon_hits + top_caps))[:8]
    if not resources:
        resources = ["record"]

    # verbs present
    verbs_present = set()
    for v, forms in VERB_MAP.items():
        if any(f in tlow for f in forms):
            verbs_present.add(v)
    if not verbs_present:
        verbs_present = {"read"}  # minimal

    # security hints
    security = "oauth2" if any(x in tlow for x in ["oauth2", "oidc", "open id connect", "jwt", "scope"]) else \
               "apiKey" if "api key" in tlow or "apikey" in tlow else "none"

    return resources, verbs_present, security

def build_openapi(resources, verbs, security):
    sec_schemes = {}
    sec_req = []
    if security == "oauth2":
        sec_schemes = {
            "OAuth2": {
                "type": "oauth2",
                "flows": {
                    "clientCredentials": {
                        "tokenUrl": "https://auth.company.com/oauth2/token",
                        "scopes": {"read": "Read data", "write": "Write data"}
                    }
                }
            }
        }
        sec_req = [{"OAuth2": ["read", "write"]}]
    elif security == "apiKey":
        sec_schemes = {"ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}
        sec_req = [{"ApiKeyAuth": []}]
    else:
        sec_schemes, sec_req = {}, []

    def crud_ops(resource):
        ops = {}
        r = resource.lower()
        # collection
        if "read" in verbs:
            ops["get"] = {
                "summary": f"List {r}s",
                "parameters": [
                    {"name":"page","in":"query","schema":{"type":"integer"},"description":"Page number"},
                    {"name":"pageSize","in":"query","schema":{"type":"integer"},"description":"Items per page"},
                    {"name":"q","in":"query","schema":{"type":"string"},"description":"Search query"}
                ],
                "responses":{"200":{"description":"OK"}}
            }
        if "create" in verbs:
            ops["post"] = {
                "summary": f"Create {r}",
                "requestBody":{
                    "required": True,
                    "content":{"application/json":{"schema":{"type":"object"}}}
                },
                "responses":{"201":{"description":"Created"}}
            }
        # item
        item_ops = {}
        if "read" in verbs:
            item_ops["get"] = {"summary": f"Get {r} by id","responses":{"200":{"description":"OK"},"404":{"description":"Not Found"}}}
        if "update" in verbs:
            item_ops["patch"] = {
                "summary": f"Update {r}",
                "requestBody":{"required": True,"content":{"application/json":{"schema":{"type":"object"}}}},
                "responses":{"200":{"description":"Updated"}}
            }
        if "delete" in verbs:
            item_ops["delete"] = {"summary": f"Delete {r}","responses":{"204":{"description":"Deleted"}}}
        return ops, item_ops

    paths = {}
    for res in resources:
        r = singularize(res.lower())
        coll_path = f"/{r}s"
        item_path = f"/{r}s/{{id}}"
        ops_coll, ops_item = crud_ops(r)
        if ops_coll:
            paths[coll_path] = ops_coll
        if ops_item:
            paths[item_path] = ops_item

    openapi = {
        "openapi": "3.0.0",
        "info": {
            "title": "RFP-Derived Enterprise API",
            "description": "Auto-generated from RFP content using heuristic reasoning (no external LLM).",
            "version": "1.0.0"
        },
        "servers": [{"url": "https://api.company.com/v1"}],
        "components": {"securitySchemes": sec_schemes},
        "security": sec_req,
        "paths": paths
    }
    return openapi

def agent_api_json(text, a3, a4):
    resources, verbs, security = detect_resources_and_verbs(text)
    # if integration is strong but no verbs, assume read+create
    if "Likely" in a3["label"] and not verbs:
        verbs = {"read", "create"}
    # if standards found, keep as-is; else keep security minimal
    if "No Standards" in a4["label"] and security == "none":
        security = "apiKey"  # pragmatic default for enterprises

    spec = build_openapi(resources, verbs, security)
    rationale = {
        "resources_detected": resources,
        "verbs_detected": sorted(list(verbs)),
        "security_mode": security,
        "notes": "Resources inferred from enterprise canon + capitalized domain nouns; verbs from action words; security from standards hints."
    }
    return spec, rationale

# ---------- Streamlit UI ----------
uploaded_file = st.file_uploader("Upload RFP (PDF)", type="pdf")
if uploaded_file:
    text = extract_text(uploaded_file)
    if not text.strip():
        st.error("Could not read text from PDF. Please ensure the PDF has selectable text (not only images).")
    else:
        col1, col2 = st.columns([2,1])

        with col1:
            st.subheader("Agent Results & Rationales")
            a1 = agent_business_scope(text)
            a2 = agent_api_explicit(text)
            a3 = agent_integration(text)
            a4 = agent_compliance(text)
            a5 = agent_summary(a1, a2, a3, a4)
            spec, rationale6 = agent_api_json(text, a3, a4)

            def show_agent(title, payload):
                st.markdown(f"**{title}**")
                st.json(payload)

            show_agent("Agent 1 — Business Scope", a1)
            show_agent("Agent 2 — Explicit API Mentions", a2)
            show_agent("Agent 3 — Integration & Interoperability", a3)
            show_agent("Agent 4 — Standards, Security, Compliance", a4)
            st.markdown("**Agent 5 — Decision Summary (Voting)**")
            st.json(a5)

            st.subheader("Agent 6 — Generated OpenAPI (Dynamic)")
            st.code(json.dumps(spec, indent=2), language="json")

        with col2:
            st.subheader("Quick Insights")
            kws = top_keywords(text, k=25)
            st.write("**Top keywords (heuristic):**")
            st.write(", ".join(k for k,_ in kws))

            # Download button
            spec_str = json.dumps(spec, indent=2)
            st.download_button(
                label="⬇️ Download OpenAPI JSON",
                data=spec_str.encode("utf-8"),
                file_name="rfp_derived_openapi.json",
                mime="application/json"
            )

            st.info("Tip: Paste the JSON into Swagger Editor to visualize the API.")

else:
    st.caption("Upload a PDF RFP to start the analysis.")
