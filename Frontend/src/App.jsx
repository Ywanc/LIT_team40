import { useState } from 'react'
import './App.css'
import TextareaAutosize from 'react-textarea-autosize';

const LAWNET_ORIGIN = 'https://www.lawnet.com';

const SAMPLE_TEXT =
  'In Spandeck Engineering v DSTA [2007] SGCA 37, the Court of Appeal held that a single ' +
  'two-stage test, preceded by a threshold requirement of factual foreseeability, applies to ' +
  'determine a duty of care. The court stated that "a coherent and workable test can be ' +
  'fashioned out of the basic two-stage test premised on proximity and policy considerations". ' +
  'The court also held that DSTA owed Spandeck a duty of care and awarded damages.' +
  'There is also the case of Lim Meng Suang and another v Attorney-General, and PP v John Wick.';

/* ---------- helpers ---------- */

function statusTone(status) {
  if (!status) return 'neutral';
  if (
    status === 'Queued' ||
    status === 'Searching case in LawNet' ||
    status === 'Verifying statements'
  ) return 'pending';
  if (status === 'Case found') return 'ok';
  if (status.startsWith('Case found')) return 'warn';
  if (status === 'Multiple cases found') return 'warn';
  if (status === 'No case found') return 'bad';
  return 'neutral';
}

function statusIcon(tone) {
  return { ok: '✓', warn: '!', bad: '✕', pending: '…', neutral: '•' }[tone] || '•';
}

function isNotFound(item) {
  return item.status === 'No case found';
}

function verdictTone(verdict) {
  return {
    supported: 'ok',
    partially_supported: 'warn',
    contradicted: 'bad',
    unsure: 'neutral',
  }[verdict] || 'neutral';
}

function verdictLabel(verdict) {
  return {
    supported: 'Supported',
    partially_supported: 'Partially supported',
    contradicted: 'Not supported',
    unsure: 'Unsure',
  }[verdict] || verdict;
}

function quoteTone(status) {
  return { exact: 'ok', near_match: 'warn', not_found: 'bad' }[status] || 'neutral';
}

function quoteLabel(status) {
  return {
    exact: 'Verbatim match',
    near_match: 'Near match',
    not_found: 'Not found',
  }[status] || status;
}

function formatCost(cost) {
  if (cost == null) return null;
  if (cost === 0) return '$0.00';
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  return `$${cost.toFixed(2)}`;
}

function formatNeutral(neutral) {
  if (!neutral || !neutral.year) return null;
  return `[${neutral.year}] ${(neutral.court || '').toUpperCase()} ${neutral.number || ''}`.trim();
}

function formatReported(reported) {
  if (!reported || !reported.year) return null;
  return `[${reported.year}] ${reported.volume || ''} ${reported.report || ''} ${reported.page || ''}`
    .replace(/\s+/g, ' ')
    .trim();
}

function formatClaimedCitation(claimed) {
  const c = claimed?.citation;
  if (!c) return null;
  if (typeof c === 'string') return c;
  if (c.citation_type === 'neutral') return formatNeutral(c);
  if (c.citation_type === 'reported') return formatReported(c);
  return null;
}

function bestCitation(item) {
  const cits = item.actual_citations || {};
  return formatNeutral(cits.neutral) || formatReported(cits.reported) || formatClaimedCitation(item.claimed_metadata) || '—';
}

function courtOf(item) {
  const neutral = (item.actual_citations || {}).neutral;
  return neutral?.court ? neutral.court.toUpperCase() : '—';
}

function yearOf(item) {
  const cits = item.actual_citations || {};
  return cits.neutral?.year || cits.reported?.year || item.claimed_metadata?.citation?.year || '—';
}

function caseTitle(item) {
  return item.matched_search_title || item.claimed_metadata?.title || 'Unknown case';
}

function judgmentUrl(item) {
  if (item.claim_check?.url) return item.claim_check.url;
  if (item.matched_search_href) return LAWNET_ORIGIN + item.matched_search_href;
  return null;
}

/* ---------- small components ---------- */

function Badge({ tone, children, icon }) {
  return (
    <span className={`badge ${tone}`}>
      {icon && <span className="icon">{icon}</span>}
      {children}
    </span>
  );
}

function ProgressSteps({ step }) {
  const steps = ['Extracting case mentions']
  return (
    <div className="progress-steps">
      {steps.map((label, i) => (
        <span key={label} style={{ display: 'contents' }}>
          <span className={`step ${i === step ? 'active' : i < step ? 'done' : ''}`}>
            <span className="dot" />
            {label}
          </span>
          {i < steps.length - 1 && <span className="sep">›</span>}
        </span>
      ))}
    </div>
  );
}

function phaseToStep(phase) {
  if (phase === 'extracting') return 0;
  if (phase === 'searching') return 1;
  if (phase === 'verifying_statements') return 2;
  return 0;
}

function applyCaseUpdate(cases, event) {
  if (event.index == null || !cases[event.index]) return cases;
  const next = cases.slice();
  const prev = next[event.index];

  if (event.result) {
    next[event.index] = {
      ...event.result,
      claim_check: event.result.claim_check ?? prev.claim_check ?? null,
      statements: event.result.statements ?? prev.statements ?? [],
      _pending: false,
    };
    return next;
  }

  next[event.index] = {
    ...prev,
    ...(event.status != null ? { status: event.status } : {}),
    ...(event.claim_check != null ? { claim_check: event.claim_check } : {}),
    _pending:
      event.status === 'Queued' ||
      event.status === 'Searching case in LawNet' ||
      event.status === 'Verifying statements',
  };
  return next;
}

async function readAuditStream(res, onEvent) {
  if (!res.body) throw new Error('Streaming not supported by this browser.');
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const chunks = buffer.split('\n\n');
    buffer = chunks.pop() || '';

    for (const chunk of chunks) {
      const line = chunk
        .split('\n')
        .map((l) => l.trim())
        .find((l) => l.startsWith('data:'));
      if (!line) continue;
      const payload = line.slice(5).trim();
      if (!payload || payload === '[DONE]') continue;
      onEvent(JSON.parse(payload));
    }
  }
}

function Stat({ tone, label, value }) {
  return (
    <span className="stat">
      <span className={`swatch ${tone}`} />
      <strong>{value}</strong> {label}
    </span>
  );
}

/* ---------- claim check ---------- */

function ellipsizeQuote(text) {
  const t = String(text || '').replace(/\s+/g, ' ').trim();
  if (!t) return '—';
  return `…${t}…`;
}

function CiteItem({ para, quote, fullText }) {
  const [expanded, setExpanded] = useState(false);
  const full = String(fullText || '').trim();
  const snippet = String(quote || '').trim() || full;
  const canExpand = !!full && full !== snippet;

  const handleToggle = (e) => {
    if (!canExpand) return;
    e.stopPropagation();
    setExpanded((v) => !v);
  };

  return (
    <li
      className={`cite-item${canExpand ? ' expandable' : ''}${expanded ? ' open' : ''}`}
      onClick={handleToggle}
      onKeyDown={(e) => {
        if (!canExpand) return;
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          handleToggle(e);
        }
      }}
      role={canExpand ? 'button' : undefined}
      tabIndex={canExpand ? 0 : undefined}
      title={canExpand ? (expanded ? 'Click to collapse' : 'Click to show full paragraph') : undefined}
    >
      {para != null && <span className="para-ref">{para === 'HN' ? 'HN' : `[${para}]`}</span>}
      <span className="cite-quote">
        {expanded && full ? full : ellipsizeQuote(snippet)}
        {canExpand && <span className="cite-expand-hint">{expanded ? 'less' : 'more'}</span>}
      </span>
    </li>
  );
}

function StatementCard({ s }) {
  const tone = verdictTone(s.verdict);
  return (
    <div className={`claim ${tone}`}>
      <div className="claim-head">
        <p className="claim-statement">{s.statement}</p>
        <Badge tone={tone} icon={statusIcon(tone)}>{verdictLabel(s.verdict)}</Badge>
      </div>

      {s.citations?.length > 0 && (
        <ul className="citations">
          {s.citations.map((c, i) => (
            <CiteItem key={i} para={c.para} quote={c.quote} fullText={c.matched_text} />
          ))}
        </ul>
      )}

      {s.reasoning && (
        <details className="reasoning">
          <summary>Reasoning</summary>
          <p className="reason">{s.reasoning}</p>
        </details>
      )}
    </div>
  );
}

function QuoteCard({ q }) {
  const tone = quoteTone(q.status);
  return (
    <div className={`claim ${tone}`}>
      <div className="claim-head">
        <p className="claim-statement">{q.quote}</p>
        <Badge tone={tone} icon={statusIcon(tone)}>{quoteLabel(q.status)}</Badge>
      </div>

      {q.para != null && q.matched_text && (
        <ul className="citations">
          <CiteItem para={q.para} quote={q.quote} fullText={q.matched_text} />
        </ul>
      )}
    </div>
  );
}

function PendingStatementList({ statements }) {
  if (!statements?.length) return null;
  return (
    <div className="claim-list">
      {statements.map((statement, i) => (
        <div key={i} className="claim pending">
          <div className="claim-head">
            <p className="claim-statement">{statement}</p>
            <Badge tone="pending"><span className="badge-spinner" /> Checking…</Badge>
          </div>
        </div>
      ))}
    </div>
  );
}

function ClaimCheck({ cc, enabled, pending, statements }) {
  if (!enabled) {
    return (
      <div className="claim-section">
        <h3>Statement check</h3>
        <p className="alert alert-info" style={{ marginTop: 0 }}>
          Enable “Fact-check statements” before searching to verify what the AI said about this case.
        </p>
      </div>
    );
  }

  if (pending) {
    return (
      <div className="claim-section">
        <h3>Statement check <Badge tone="pending"><span className="badge-spinner" /> Verifying…</Badge></h3>
        <p className="reason">Checking asserted holdings and quotes against the judgment.</p>
        <PendingStatementList statements={statements} />
      </div>
    );
  }

  if (!cc) {
    return (
      <div className="claim-section">
        <h3>Statement check <Badge tone="neutral">Not run</Badge></h3>
        <p className="reason">Only cases resolved to a single LawNet judgment are fact-checked.</p>
      </div>
    );
  }

  if (cc.status === 'skipped') {
    return (
      <div className="claim-section">
        <h3>Statement check <Badge tone="neutral">Skipped</Badge></h3>
        <p className="reason">{cc.reason}</p>
      </div>
    );
  }

  if (cc.status === 'error') {
    return (
      <div className="claim-section">
        <h3>Statement check <Badge tone="bad">Error</Badge></h3>
        <p className="reason">{cc.error}</p>
      </div>
    );
  }

  const summary = cc.summary?.statements || {};
  const quoteSummary = cc.summary?.quotes || {};
  return (
    <div className="claim-section">
      {cc.quotes?.length > 0 && (
        <>
          <h3>
            Direct quotes
            {quoteSummary.exact > 0 && <Badge tone="ok">{quoteSummary.exact} verbatim</Badge>}
            {quoteSummary.near_match > 0 && <Badge tone="warn">{quoteSummary.near_match} near match</Badge>}
            {quoteSummary.not_found > 0 && <Badge tone="bad">{quoteSummary.not_found} not found</Badge>}
          </h3>
          <div className="claim-list">
            {cc.quotes.map((q, i) => <QuoteCard key={i} q={q} />)}
          </div>
        </>
      )}

      <h3 style={{ marginTop: cc.quotes?.length > 0 ? 18 : 0 }}>
        Statement check
        {summary.supported > 0 && <Badge tone="ok">{summary.supported} supported</Badge>}
        {summary.partially_supported > 0 && <Badge tone="warn">{summary.partially_supported} partial</Badge>}
        {summary.contradicted > 0 && <Badge tone="bad">{summary.contradicted} not supported</Badge>}
        {summary.unsure > 0 && <Badge tone="neutral">{summary.unsure} unsure</Badge>}
      </h3>

      {cc.statements?.length > 0 ? (
        <div className="claim-list">
          {cc.statements.map((s, i) => <StatementCard key={i} s={s} />)}
        </div>
      ) : (
        <p className="reason">No statements were attributed to this case.</p>
      )}
    </div>
  );
}

/* ---------- case detail ---------- */

function CaseDetail({ item, claimsEnabled }) {
  const claimedCit = formatClaimedCitation(item.claimed_metadata);
  const cits = item.actual_citations || {};
  const jUrl = judgmentUrl(item);
  const isMultiple = item.status === 'Multiple cases found';

  return (
    <div className="detail">
      {!isMultiple && (
        <div className="detail-grid">
          <div className="panel">
            <h4>What the AI cited</h4>
            <dl className="kv">
              <dt>Case</dt><dd>{item.claimed_metadata?.title || '—'}</dd>
              <dt>Citation</dt><dd>{claimedCit || <span className="cell muted">none given</span>}</dd>
            </dl>
          </div>

          <div className="panel">
            <h4>LawNet match</h4>
            {item.matched_search_title ? (
              <dl className="kv">
                <dt>Judgment</dt><dd>{item.matched_search_title}</dd>
                <dt>Neutral</dt><dd>{formatNeutral(cits.neutral) || '—'}</dd>
                {formatReported(cits.reported) && (<><dt>Reported</dt><dd>{formatReported(cits.reported)}</dd></>)}
                <dt>Citation</dt>
                <dd>
                  {item.citation_verified === true && <Badge tone="ok" icon="✓">Matches</Badge>}
                  {item.citation_verified === false && <Badge tone="warn" icon="!">Does not match</Badge>}
                  {item.citation_verified == null && <span className="cell muted">not checked</span>}
                </dd>
              </dl>
            ) : (
              <p className="reason">{item.reason || item.error || 'No single judgment could be matched.'}</p>
            )}
            <div className="links" style={{ marginTop: 10 }}>
              {jUrl && <a href={jUrl} target="_blank" rel="noreferrer">Open judgment ↗</a>}
            </div>
          </div>
        </div>
      )}

      {item.candidates?.length > 0 && (
        <div className="panel">
          <h4>{item.matched_search_title ? 'Other candidates' : isMultiple ? 'Matching cases' : 'Closest LawNet results'}</h4>
          {item.reason && item.matched_search_title == null && <p className="reason" style={{ marginBottom: 10 }}>{item.reason}</p>}
          <ul className="candidates">
            {item.candidates.map((c, i) => (
              <li key={i}>
                <span>
                  {c.href
                    ? <a href={LAWNET_ORIGIN + c.href} target="_blank" rel="noreferrer">{c.title}</a>
                    : c.title}
                </span>
                <span className="cit">{formatNeutral(c.citations?.neutral) || formatReported(c.citations?.reported) || ''}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <ClaimCheck
        cc={item.claim_check}
        enabled={claimsEnabled}
        pending={item.status === 'Verifying statements'}
        statements={item.statements}
      />
    </div>
  );
}

/* ---------- app ---------- */

function App() {
  const [value, setValue] = useState('');
  const [checkStatements, setCheckStatements] = useState(true);
  const [loading, setLoading] = useState(false);
  const [step, setStep] = useState(0);
  const [error, setError] = useState('');
  const [data, setData] = useState(null);
  const [openIndex, setOpenIndex] = useState(null);
  const [showCost, setShowCost] = useState(false);

  const handleSearch = async () => {
    if (!value.trim()) {
      setError('Paste some AI-generated text to check.');
      return;
    }
    setStep(0);
    setLoading(true);
    setError('');
    setData(null);
    setOpenIndex(null);
    setShowCost(false);

    try {
      const res = await fetch('/api/audit/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({ text: value, max_workers: 5, check_statements: checkStatements }),
      });
      if (!res.ok) throw new Error(`Server responded with status ${res.status}`);

      await readAuditStream(res, (event) => {
        if (event.type === 'phase') {
          setStep(phaseToStep(event.phase));
          return;
        }

        if (event.type === 'extracted') {
          setData({
            extracted_count: event.count,
            verified_count: 0,
            statements_checked: event.statements_checked,
            cases: event.cases || [],
            total_cost: event.total_cost ?? 0,
          });
          return;
        }

        if (event.type === 'case_update') {
          setData((prev) => {
            if (!prev) return prev;
            return {
              ...prev,
              cases: applyCaseUpdate(prev.cases, event),
              total_cost: event.total_cost ?? prev.total_cost,
            };
          });
          return;
        }

        if (event.type === 'done') {
          setData({
            extracted_count: event.extracted_count,
            verified_count: event.verified_count,
            statements_checked: event.statements_checked,
            cases: event.cases || [],
            total_cost: event.total_cost ?? 0,
          });
          return;
        }

        if (event.type === 'error') {
          setError(event.message || 'Audit failed.');
        }
      });
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  const results = data?.cases || [];
  const counts = {
    ok: results.filter((c) => statusTone(c.status) === 'ok').length,
    warn: results.filter((c) => statusTone(c.status) === 'warn').length,
    bad: results.filter((c) => statusTone(c.status) === 'bad').length,
    pending: results.filter((c) => statusTone(c.status) === 'pending').length,
    neutral: results.filter((c) => statusTone(c.status) === 'neutral').length,
  };

  return (
    <div className="page">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">CC</div>
          <div>
            <div className="brand-name">CiteCheck</div>
            <div className="brand-tag">AI Output Case Checker | LawNet OpenLaw</div>
          </div>
        </div>
      </header>

      <section className="hero">
        <h1>Don’t just generate. Verify.</h1>
        <p>CiteCheck confirms the existence of cases mentioned, and fact-checks claims regarding the case.</p>
      </section>

      <section className="card input-card">
        <TextareaAutosize
          minRows={9}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Paste the AI-generated legal text here…"
          disabled={loading}
        />
        <div className="input-footer">
          <div className="input-meta">
            <label className="toggle">
              <input
                type="checkbox"
                checked={checkStatements}
                onChange={(e) => setCheckStatements(e.target.checked)}
                disabled={loading}
              />
              Fact-check statements regarding the case
            </label>
            {/* <span>{value.length.toLocaleString()} chars</span> */}
            {!value && !loading && (
              <a href="#" onClick={(e) => { e.preventDefault(); setValue(SAMPLE_TEXT); }}>Try a sample</a>
            )}
          </div>
          <button type="button" className="btn-primary" onClick={handleSearch} disabled={loading}>
            {loading && <span className="spinner" />}
            {loading ? 'Verifying…' : 'Verify'}
          </button>
        </div>

        {loading && (
          <div className="alert alert-info">
            <ProgressSteps step={step} />
          </div>
        )}
        {error && <div className="alert alert-error">{error}</div>}
      </section>

      {data && (
        <section className="card results-card">
          <div className="results-head">
            <div>
              <h2>Analysis</h2>
              <p className="subtitle">
                {data.extracted_count} case mention{data.extracted_count === 1 ? '' : 's'} extracted
                {loading
                  ? ' · working…'
                  : data.statements_checked
                    ? ' · statements fact-checked'
                    : ''}
              </p>
            </div>
            <div className="stats">
              <Stat tone="ok" value={counts.ok} label="verified" />
              <Stat tone="warn" value={counts.warn} label="needs review" />
              <Stat tone="bad" value={counts.bad} label="not found" />
              {counts.pending > 0 && <Stat tone="pending" value={counts.pending} label="in progress" />}
              {counts.neutral > 0 && <Stat tone="neutral" value={counts.neutral} label="errors" />}
            </div>
          </div>

          {results.length === 0 ? (
            <div className="empty-state">No case citations were detected in the text.</div>
          ) : (
            <div className="table">
              <div className="table-row table-head">
                <div>Case</div>
                <div className="right">Citation</div>
                <div className="right">Court</div>
                <div className="right">Year</div>
                <div className="right">Jurisdiction</div>
                <div>Status</div>
              </div>

              {results.map((item, index) => {
                const tone = statusTone(item.status);
                const notFound = isNotFound(item);
                const isOpen = openIndex === index && !notFound;

                return (
                  <div key={index}>
                    <div
                      className={`table-row case-row ${tone} ${isOpen ? 'open' : ''} ${notFound ? 'no-toggle' : ''}`}
                      onClick={notFound ? undefined : () => setOpenIndex(isOpen ? null : index)}
                      role={notFound ? undefined : 'button'}
                      tabIndex={notFound ? undefined : 0}
                      onKeyDown={notFound ? undefined : (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpenIndex(isOpen ? null : index); } }}
                    >
                      <div className="case-cell">
                        {notFound
                          ? <span className="chevron-spacer" />
                          : <span className="chevron">▼</span>}
                        <div style={{ minWidth: 0 }}>
                          <div className="case-title">{caseTitle(item)}</div>
                        </div>
                      </div>
                      <div className="cell mono right">{bestCitation(item)}</div>
                      <div className="cell right">{courtOf(item)}</div>
                      <div className="cell right">{yearOf(item)}</div>
                      <div className="cell muted right">{notFound ? '—' : 'Singapore'}</div>
                      <div className="cell" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        <Badge tone={tone} icon={statusTone(item.status) === 'pending' ? null : statusIcon(tone)}>
                          {statusTone(item.status) === 'pending' && <span className="badge-spinner" />}
                          {item.status}
                        </Badge>
                      </div>
                    </div>

                    {isOpen && <CaseDetail item={item} claimsEnabled={!!data.statements_checked} />}
                  </div>
                );
              })}
            </div>
          )}
        </section>
      )}

      <footer className="footer">
        Results are drawn from LawNet OpenLaw. LLM verdicts are assistive — always read the cited paragraphs.
      </footer>

      {data && (
        <div className="cost-widget">
          <button
            type="button"
            className="cost-toggle"
            onClick={() => setShowCost((v) => !v)}
            title="Estimated OpenRouter LLM cost for this query"
          >
            {showCost ? formatCost(data.total_cost) || '$0.00' : 'Show cost'}
          </button>
        </div>
      )}
    </div>
  );
}

export default App