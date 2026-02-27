"use client";

import { useMemo, useState } from "react";

type Claim = { claim: string; verifiable: boolean; verify_with: string };

type Analysis = {
  signal_score: number;
  substance_score: number;
  fluff_percent: number;
  risk_flags: string[];
  verdict: string;
  missing_info_questions: string[];
  claims: Claim[];
  proof: Record<string, any>;
};

function clamp(n: number) {
  return Math.max(0, Math.min(100, n));
}

export default function Page() {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

  const [text, setText] = useState("");
  const [context, setContext] = useState("");
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<Analysis | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const canRun = useMemo(() => text.trim().length >= 5 && !loading, [text, loading]);

  async function onPaste() {
    try {
      const clip = await navigator.clipboard.readText();
      if (clip) setText(clip);
    } catch {
      // ignore
    }
  }

  async function analyze() {
    setErr(null);
    setLoading(true);
    setData(null);

    try {
      const res = await fetch(`${apiUrl}/analyze`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ content: text, context, strict: true }),
      });

      if (!res.ok) {
        const msg = await res.text();
        throw new Error(msg || `http ${res.status}`);
      }

      const json = (await res.json()) as Analysis;
      setData(json);
    } catch (e: any) {
      setErr(e?.message || "failed to fetch");
    } finally {
      setLoading(false);
    }
  }

  function copy(v: any) {
    navigator.clipboard.writeText(typeof v === "string" ? v : JSON.stringify(v, null, 2));
  }

  return (
    <div className="bg">
      <main className="wrap">
        <div className="center">
          <div className="card">
            <div className="badge">🛡 verifiable inference</div>

            <h1 className="h1">
              alpha filter <span>agent</span>
            </h1>

            {/* NEW: better description under name */}
            <p className="desc">
              score any crypto announcement in seconds — separate real signal from marketing noise, highlight missing details, and flag risks before you ape.
            </p>

            <div className="sub">⚡ is this real alpha or just marketing?</div>

            <div className="textareaWrap">
              <textarea
                className="textarea"
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="paste a tweet, announcement, or partnership news here..."
              />
              <button className="pasteBtn" onClick={onPaste} title="paste from clipboard" aria-label="paste">
                📋
              </button>
            </div>

            {/* optional context input (small but useful) */}
            <div style={{ width: "min(660px, 100%)", margin: "12px auto 0" }}>
              <input
                value={context}
                onChange={(e) => setContext(e.target.value)}
                placeholder="optional context (project / chain / link summary)"
                style={{
                  width: "100%",
                  padding: "14px 16px",
                  borderRadius: 14,
                  border: "1px solid rgba(255,255,255,.10)",
                  background: "rgba(255,255,255,.03)",
                  color: "rgba(255,255,255,.85)",
                  outline: "none",
                  fontSize: 14,
                }}
              />
            </div>

            <button className="btn" onClick={analyze} disabled={!canRun}>
              {loading ? "filtering..." : "filter alpha"}
            </button>

            <div className="note">
              tip: paste a tweet + add a short context like “this is a partnership announcement” for sharper scoring.
            </div>

            {err && (
              <div className="result" style={{ borderColor: "rgba(255,80,80,.35)" }}>
                <div className="sectionTitle">error</div>
                <div className="smallmuted">{err}</div>
                <div className="smallmuted" style={{ marginTop: 10 }}>
                  check that your backend is reachable at <b>{apiUrl}</b> and `/health` returns ok.
                </div>
              </div>
            )}

            {data && (
              <div className="result">
                <div className="row">
                  <div>
                    <div className="sectionTitle" style={{ margin: 0 }}>
                      report
                    </div>
                    <div className="smallmuted">
                      verified: <b>{String(data.proof?.verification || "n/a")}</b> · model: <b>{String(data.proof?.model || "n/a")}</b> · receipt:{" "}
                      <b>{String(data.proof?.receipt_id || "n/a")}</b>
                    </div>
                  </div>

                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                    <button className="copyBtn" onClick={() => copy(data.verdict)}>
                      copy verdict
                    </button>
                    <button className="copyBtn" onClick={() => copy(data)}>
                      copy json
                    </button>
                  </div>
                </div>

                <div className="grid" style={{ marginTop: 14 }}>
                  <div className="kpi">
                    <div className="kpiLabel">signal vs hype</div>
                    <div className="kpiValue">{data.signal_score}</div>
                    <div className="bar">
                      <div className="fill" style={{ width: `${clamp(data.signal_score)}%` }} />
                    </div>
                  </div>

                  <div className="kpi">
                    <div className="kpiLabel">technical substance</div>
                    <div className="kpiValue">{data.substance_score}</div>
                    <div className="bar">
                      <div className="fill" style={{ width: `${clamp(data.substance_score)}%` }} />
                    </div>
                  </div>

                  <div className="kpi">
                    <div className="kpiLabel">marketing fluff %</div>
                    <div className="kpiValue">{data.fluff_percent}</div>
                    <div className="bar">
                      <div className="fill" style={{ width: `${clamp(data.fluff_percent)}%` }} />
                    </div>
                  </div>
                </div>

                <div className="sectionTitle">verdict</div>
                <div style={{ color: "rgba(255,255,255,.86)", lineHeight: 1.6 }}>{data.verdict}</div>

                <div className="sectionTitle">risk flags</div>
                <div className="pills">
                  {data.risk_flags?.length ? data.risk_flags.map((f) => <span key={f} className="pill">{f}</span>) : <span className="smallmuted">none</span>}
                </div>

                <div className="sectionTitle">missing info questions</div>
                <ul style={{ margin: 0, paddingLeft: 18, color: "rgba(255,255,255,.78)", lineHeight: 1.65 }}>
                  {(data.missing_info_questions || []).map((q, i) => (
                    <li key={i}>{q}</li>
                  ))}
                </ul>

                <div className="sectionTitle">claims extracted</div>
                <div style={{ display: "grid", gap: 10 }}>
                  {(data.claims || []).slice(0, 8).map((c, i) => (
                    <div
                      key={i}
                      style={{
                        border: "1px solid rgba(255,255,255,.10)",
                        borderRadius: 16,
                        padding: 14,
                        background: "rgba(0,0,0,.14)",
                      }}
                    >
                      <div style={{ fontWeight: 800, color: "rgba(255,255,255,.88)" }}>{c.claim}</div>
                      <div className="smallmuted" style={{ marginTop: 6 }}>
                        verifiable: <b>{String(c.verifiable)}</b> · verify with: {c.verify_with}
                      </div>
                    </div>
                  ))}
                </div>

                <div className="sectionTitle">proof</div>
                <pre
                  style={{
                    margin: 0,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    background: "rgba(255,255,255,.03)",
                    border: "1px solid rgba(255,255,255,.08)",
                    padding: 14,
                    borderRadius: 16,
                    color: "rgba(255,255,255,.75)",
                    fontSize: 12,
                  }}
                >
{JSON.stringify(data.proof, null, 2)}
                </pre>
              </div>
            )}
          </div>
        </div>

        <footer className="footer">
          built by{" "}
          <a href="https://x.com/0xnald" target="_blank" rel="noreferrer">
            nald
          </a>{" "}
          | powered by{" "}
          <a href="https://x.com/OpenGradient" target="_blank" rel="noreferrer">
            opengradient
          </a>
        </footer>
      </main>
    </div>
  );
}
