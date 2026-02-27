import json
import re
import uuid
import os
import time
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import opengradient as og

load_dotenv()

# -----------------------------
# config
# -----------------------------
OG_PRIVATE_KEY = os.environ.get("OG_PRIVATE_KEY")
if not OG_PRIVATE_KEY:
    raise RuntimeError("missing OG_PRIVATE_KEY in server/.env")

OG_TEE_LLM_MODEL = os.environ.get("OG_TEE_LLM_MODEL", "GPT_4O")
OG_OPG_APPROVAL_AMOUNT = float(os.environ.get("OG_OPG_APPROVAL_AMOUNT", "5.0"))
OG_X402_SETTLEMENT_MODE = os.environ.get("OG_X402_SETTLEMENT_MODE", "SETTLE_METADATA")

# map env string -> sdk enum (keep it strict so you don't silently misconfigure)
TEE_LLM_MAP = {
    "GPT_4O": og.TEE_LLM.GPT_4O,
    "O4_MINI": getattr(og.TEE_LLM, "O4_MINI", og.TEE_LLM.GPT_4O),  # fallback
    "GEMINI_2_0_FLASH": getattr(og.TEE_LLM, "GEMINI_2_0_FLASH", og.TEE_LLM.GPT_4O),
    "CLAUDE_3_5_HAIKU": og.TEE_LLM.CLAUDE_3_5_HAIKU,
    "CLAUDE_3_7_SONNET": getattr(og.TEE_LLM, "CLAUDE_3_7_SONNET", og.TEE_LLM.CLAUDE_3_5_HAIKU),
    "CLAUDE_4_0_SONNET": getattr(og.TEE_LLM, "CLAUDE_4_0_SONNET", og.TEE_LLM.CLAUDE_3_5_HAIKU),
}

SETTLEMENT_MAP = {
    "SETTLE": og.x402SettlementMode.SETTLE,
    "SETTLE_BATCH": og.x402SettlementMode.SETTLE_BATCH,
    "SETTLE_METADATA": og.x402SettlementMode.SETTLE_METADATA,
}

MODEL = TEE_LLM_MAP.get(OG_TEE_LLM_MODEL)
if MODEL is None:
    raise RuntimeError(f"invalid OG_TEE_LLM_MODEL={OG_TEE_LLM_MODEL}. valid: {', '.join(TEE_LLM_MAP.keys())}")

SETTLEMENT_MODE = SETTLEMENT_MAP.get(OG_X402_SETTLEMENT_MODE)
if SETTLEMENT_MODE is None:
    raise RuntimeError(
        f"invalid OG_X402_SETTLEMENT_MODE={OG_X402_SETTLEMENT_MODE}. "
        f"valid: {', '.join(SETTLEMENT_MAP.keys())}"
    )

# initialize opengradient client (route 1)
client = og.init(private_key=OG_PRIVATE_KEY)

# ensure permit2 approval once at boot (only sends tx if needed)
# documented in the llm guide.
client.llm.ensure_opg_approval(opg_amount=OG_OPG_APPROVAL_AMOUNT)

# -----------------------------
# api models
# -----------------------------
class AnalyzeRequest(BaseModel):
    content: str = Field(..., min_length=5, description="tweet/announcement text to analyze")
    context: Optional[str] = Field(None, description="optional extra context (e.g., project name, chain, link summary)")
    strict: bool = Field(True, description="if true, model must output strict json only")

class Claim(BaseModel):
    claim: str
    verifiable: bool
    verify_with: str

class AnalysisResponse(BaseModel):
    signal_score: int
    substance_score: int
    fluff_percent: int
    risk_flags: List[str]
    verdict: str
    missing_info_questions: List[str]
    claims: List[Claim]
    proof: Dict[str, Any]

# -----------------------------
# prompt
# -----------------------------
RUBRIC = """
you are an alpha filter agent for crypto announcements.

goal: classify whether text is real signal or marketing hype.

scoring rules:

signal_score (0-100):
start at 50
+15 if there are specific verifiable details (dates, chain, addresses, metrics, repo, parameters)
+15 if it explains what changed (before -> after)
+10 if it includes constraints/tradeoffs (fees, limits, risks)
-20 if mostly superlatives (revolutionary, game-changing) with no details
-15 if pure partnership name-dropping with no integration specifics
-10 if deadline fomo with no substance (soon, big news tomorrow)

substance_score (0-100):
higher if it includes mechanism, architecture, how-it-works, onchain flow, measurable claims, concrete integrations

fluff_percent (0-100):
estimate percent of text that is slogans/vibes/superlatives/emoji hype/vague claims without evidence

risk_flags (multi-label, pick any that apply):
- missing specifics
- overpromised performance
- token/airdrop bait
- security handwaving
- centralization risk
- vague partnership
- regulatory risk wording
- unverifiable claims
- vague timeline
- misleading comparison

output requirements:
return valid json only, no markdown, no extra keys.
ensure integers are within range and are ints (not strings).
"""

JSON_SCHEMA_GUIDE = """
json format:
{
  "signal_score": 0-100,
  "substance_score": 0-100,
  "fluff_percent": 0-100,
  "risk_flags": ["..."],
  "verdict": "1 short paragraph, plain english",
  "missing_info_questions": ["q1", "q2", "q3"],
  "claims": [
    {"claim":"...", "verifiable":true/false, "verify_with":"..."}
  ]
}
"""

def _build_messages(content: str, context: Optional[str], strict: bool) -> List[Dict[str, str]]:
    user_payload = f"text to analyze:\n{content.strip()}\n"
    if context:
        user_payload += f"\nextra context:\n{context.strip()}\n"

    system = RUBRIC.strip()
    if strict:
        system += "\n\n" + JSON_SCHEMA_GUIDE.strip()

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_payload},
    ]

def _extract_text(raw: Any) -> str:
    # OpenGradient chat_output may be a string OR an OpenAI-style dict
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        # common: {"choices":[{"message":{"content":"..."}}]}
        try:
            choices = raw.get("choices")
            if isinstance(choices, list) and choices:
                msg = choices[0].get("message") or {}
                content = msg.get("content")
                if isinstance(content, str):
                    return content
        except Exception:
            pass
        # fallback: some payloads may carry text elsewhere
        for k in ("content", "text", "output"):
            v = raw.get(k)
            if isinstance(v, str):
                return v
        return json.dumps(raw)
    # last resort
    return str(raw)


def _safe_json_loads(s: Any) -> Dict[str, Any]:
    # accept either string or dict; normalize to string
    if isinstance(s, dict):
        # if the model already returned a parsed dict, use it
        return s

    s = _extract_text(s).strip()
    # try direct
    try:
        return json.loads(s)
    except Exception:
        pass
    # try to slice first {...} block
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(s[start : end + 1])
    raise ValueError("could not parse json")

def _clamp_int(x: Any, lo: int, hi: int, name: str) -> int:
    """Coerce model output into an int in [lo, hi].
    Accepts ints, floats, numeric strings, and strings like '15%' or '15/100'.
    Never raises.
    """
    if x is None:
        return lo
    if isinstance(x, bool):
        return lo

    # already numeric
    if isinstance(x, int):
        return max(lo, min(hi, x))
    if isinstance(x, float):
        return max(lo, min(hi, int(round(x))))

    # string / other -> extract first number
    s = str(x).strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return lo
    try:
        n = float(m.group(0))
        return max(lo, min(hi, int(round(n))))
    except Exception:
        return lo

def _normalize_flags(flags: Any) -> List[str]:
    if not flags:
        return []
    if not isinstance(flags, list):
        return []
    out = []
    for f in flags:
        if isinstance(f, str):
            out.append(f.strip().lower())
    # de-dupe
    seen = set()
    deduped = []
    for f in out:
        if f and f not in seen:
            seen.add(f)
            deduped.append(f)
    return deduped

# -----------------------------
# app
# -----------------------------
app = FastAPI(title="alpha filter agent", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/analyze", response_model=AnalysisResponse)
def analyze(req: AnalyzeRequest):
    messages = _build_messages(req.content, req.context, req.strict)

    try:
        # direct sdk route:
        # - chat is tee verified
        # - payment_hash returned (used as proof handle)
        result = client.llm.chat(
            model=MODEL,
            messages=messages,
            max_tokens=650,
            temperature=0.0,
            x402_settlement_mode=SETTLEMENT_MODE,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"opengradient llm call failed: {e}")

    # sdk quickstart shows chat output on response.chat_output
    # llm doc also shows payment_hash on outputs
    raw = getattr(result, "chat_output", None) or getattr(result, "completion_output", None)
    if raw is None:
        raise HTTPException(status_code=500, detail="no chat_output returned from model")

    raw_text = _extract_text(raw)
    if not raw_text.strip() and isinstance(raw, dict):
        # if dict has no text, still try to parse as dict
        pass

    try:
        data = _safe_json_loads(raw_text if raw_text else raw)
    except Exception as e:
        # one retry: ask model to repair into strict json
        repair_messages = [
            {"role": "system", "content": "repair the following into valid json matching the required schema. output json only."},
            {"role": "user", "content": raw_text if "raw_text" in locals() else str(raw)},
        ]
        try:
            repair = client.llm.chat(
                model=MODEL,
                messages=repair_messages,
                max_tokens=650,
                temperature=0.0,
                x402_settlement_mode=SETTLEMENT_MODE,
            )
            repaired_raw = getattr(repair, "chat_output", None) or ""
            data = _safe_json_loads(repaired_raw)
        except Exception as e2:
            raise HTTPException(status_code=500, detail=f"json parse failed: {e} / repair failed: {e2}")

    # normalize + validate
    signal = _clamp_int(data.get("signal_score"), 0, 100, "signal_score")
    substance = _clamp_int(data.get("substance_score"), 0, 100, "substance_score")
    fluff = _clamp_int(data.get("fluff_percent"), 0, 100, "fluff_percent")

    verdict = (data.get("verdict") or "").strip()
    if not verdict:
        verdict = "no verdict returned."

    missing_q = data.get("missing_info_questions") or []
    if not isinstance(missing_q, list):
        missing_q = []
    missing_q = [q.strip() for q in missing_q if isinstance(q, str) and q.strip()][:5]

    claims_in = data.get("claims") or []
    claims: List[Claim] = []
    if isinstance(claims_in, list):
        for c in claims_in[:8]:
            if not isinstance(c, dict):
                continue
            claim_txt = (c.get("claim") or "").strip()
            verify_with = (c.get("verify_with") or "").strip()
            verifiable = bool(c.get("verifiable")) if claim_txt else False
            if claim_txt:
                claims.append(Claim(claim=claim_txt, verifiable=verifiable, verify_with=verify_with or "n/a"))

    flags = _normalize_flags(data.get("risk_flags"))

    # collect proof fields across sdk versions
    proof = {
        "verification": "tee",
        "model": str(OG_TEE_LLM_MODEL),
        "x402_settlement_mode": str(OG_X402_SETTLEMENT_MODE),
        "generated_at_unix": int(time.time()),
    }

    # best-effort: different sdk versions may expose different attribute names
    for k in [
        "payment_hash","paymentHash","x402_payment_hash","x402PaymentHash",
        "payment_id","paymentId","receipt","settlement_metadata","settlementMetadata","metadata",
        "transaction_hash","transactionHash"
    ]:
        if hasattr(result, k):
            proof[k] = getattr(result, k)

    # one canonical handle for UI
        receipt_id = str(uuid.uuid4())
    proof["receipt_id"] = receipt_id

    proof_handle = proof.get("payment_hash") or proof.get("paymentHash") or proof.get("transaction_hash") or proof.get("transactionHash")
    # if sdk returns the string "external", treat it as not available
    if isinstance(proof_handle, str) and proof_handle.lower() == "external":
        proof_handle = None
    proof["proof_handle"] = proof_handle

    # also include a list of top-level keys if the result is dict-like
    if isinstance(result, dict):
        proof["result_keys"] = list(result.keys())
    else:
        proof["result_attrs"] = [a for a in dir(result) if any(x in a.lower() for x in ["pay", "hash", "receipt", "settle", "meta"])]


    return AnalysisResponse(
        signal_score=signal,
        substance_score=substance,
        fluff_percent=fluff,
        risk_flags=flags,
        verdict=verdict,
        missing_info_questions=missing_q,
        claims=claims,
        proof=proof,
    )
