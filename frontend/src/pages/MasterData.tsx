/**
 * Master Data: the plant's dimension tables (customers, products, BOM, machines, molds, …) with create, edit and delete.
 *
 * Changes are kept per session on top of the seed: a row shows Created or Edited, a deleted seed row can be brought
 * back, and Reset returns everything to the seed. The other modules still compute from the seed. Product routings are
 * derived from other master data and stay read-only. The entries are listed here, not fetched, so the sidebar can show
 * them before the API answers.
 */

import { useMemo, useState } from "react";
import { Navigate, useParams } from "react-router-dom";
import { Badge, Card, Dialog, Source, Status } from "../components/ui";
import { ApiError, del, errorText, post, put } from "../lib/api";
import { useApp, useData } from "../lib/store";

export const MASTER_SECTIONS: { section: string; links: [string, string][] }[] = [
  { section: "Partners", links: [["customers", "Customers"], ["vendors", "Vendors"]] },
  {
    section: "Products & Process",
    links: [["products", "Products"], ["materials", "Materials"], ["bom", "Bill of Materials"], ["process-flow", "Process Flow"], ["routings", "Product Routings"]],
  },
  { section: "Facilities", links: [["machines", "Machines"], ["molds", "Molds"], ["mold-compatibility", "Mold Compatibility"], ["shifts", "Shifts"], ["warehouses", "Warehouses"]] },
  { section: "Classification", links: [["defects", "Defect Codes"], ["downtime-reasons", "Downtime Reasons"]] },
];

const KNOWN = new Set(MASTER_SECTIONS.flatMap((s) => s.links.map(([k]) => k)));

type Column = { key: string; label: string; kind: "str" | "int" | "float"; ref: string | null; required: boolean; derived: boolean };
type Row = Record<string, unknown> & { _key: string; _status?: "created" | "edited" };
type Detail = {
  key: string;
  label: string;
  section: string;
  editable: boolean;
  pk: string[];
  columns: Column[];
  options: Record<string, string[]>;
  rows: Row[];
  deleted: Row[];
  source: string;
};
type FieldError = { field: string; message: string };

const cell = (v: unknown) => (v == null || v === "" ? "–" : typeof v === "number" ? v.toLocaleString("en-US") : String(v));
const STATUS_TONE = { created: "good", edited: "info" } as const;

function fieldErrors(e: unknown): FieldError[] {
  const f = e instanceof ApiError ? (e.body.detail as { fields?: FieldError[] } | null)?.fields : null;
  return Array.isArray(f) ? f : [];
}

/** Create or edit one row: every typed column, key fields locked when editing, references picked from what exists. */
function RowDialog({ d, row, onClose, onSaved }: { d: Detail; row: Row | null; onClose: () => void; onSaved: (text: string) => void }) {
  const editing = row !== null;
  const cols = d.columns.filter((c) => !c.derived);
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(cols.map((c) => [c.key, row?.[c.key] == null ? "" : String(row[c.key])])));
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const errOf = (k: string) => errors.find((e) => e.field === k)?.message;

  const save = async () => {
    setBusy(true);
    setErrors([]);
    setMsg(null);
    try {
      const base = `/api/master-data/${d.key}`;
      const saved = editing ? await put<Row>(`${base}/${encodeURIComponent(row._key)}`, values) : await post<Row>(base, values);
      onSaved(`${d.label} ${saved._key} ${editing ? "updated" : "created"}.`);
    } catch (e) {
      setErrors(fieldErrors(e));
      setMsg(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open title={editing ? `Edit ${d.label}: ${row._key}` : `Add to ${d.label}`} onClose={onClose} wide>
      <div className="form-grid">
        {cols.map((c) => {
          const locked = editing && d.pk.includes(c.key);
          const err = errOf(c.key);
          const common = {
            "aria-label": c.label,
            value: values[c.key],
            disabled: locked || busy,
            className: err ? "invalid" : "",
            onChange: (e: { target: { value: string } }) => setValues((v) => ({ ...v, [c.key]: e.target.value })),
          };
          return (
            <label key={c.key}>
              <span>
                {c.label}
                {c.required && <span className="req" aria-hidden> *</span>}
              </span>
              {c.ref && d.options[c.key] ? (
                <select {...common}>
                  <option value="">{c.required ? "Choose…" : "None"}</option>
                  {d.options[c.key].map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              ) : (
                <input {...common} type={c.kind === "str" ? "text" : "number"} min={c.kind === "str" ? undefined : 0} step={c.kind === "float" ? "any" : c.kind === "int" ? 1 : undefined} />
              )}
              {err && <span className="field-error">{err}</span>}
            </label>
          );
        })}
      </div>
      {d.columns.some((c) => c.derived) && (
        <p className="muted small">{d.columns.filter((c) => c.derived).map((c) => c.label).join(", ")} are filled from what this row references.</p>
      )}
      {msg && <div className="notice-box bad" role="alert">{msg}</div>}
      <div className="dialog-foot">
        <button onClick={onClose} disabled={busy}>Cancel</button>
        <button className="primary" onClick={save} disabled={busy}>{busy ? "Saving…" : editing ? "Save changes" : "Add"}</button>
      </div>
    </Dialog>
  );
}

function DeleteDialog({ d, row, onClose, onDone }: { d: Detail; row: Row; onClose: () => void; onDone: (text: string) => void }) {
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try {
      await del(`/api/master-data/${d.key}/${encodeURIComponent(row._key)}`);
      onDone(`${d.label} ${row._key} deleted.${row._status === "created" ? "" : " Restore it from the deleted rows below."}`);
    } catch (e) {
      setMsg(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open title={`Delete ${row._key}?`} onClose={onClose}>
      <p>
        {row._status === "created"
          ? `${row._key} was added in this session; deleting it removes it.`
          : `${row._key} is hidden from ${d.label} in this session. You can restore it until you reset.`}
      </p>
      {msg && <div className="notice-box bad" role="alert">{msg}</div>}
      <div className="dialog-foot">
        <button onClick={onClose} disabled={busy}>Cancel</button>
        <button className="primary danger" onClick={go} disabled={busy}>{busy ? "Deleting…" : "Delete"}</button>
      </div>
    </Dialog>
  );
}

export default function MasterDataPage() {
  const { entity = "" } = useParams();
  const { bump } = useApp();
  // the search and the open dialog belong to one entity: opening another starts clean
  const [ui, setUi] = useState<{ entity: string; q: string; edit: Row | null | "new"; remove: Row | null }>({ entity, q: "", edit: null, remove: null });
  const s = ui.entity === entity ? ui : { entity, q: "", edit: null, remove: null };
  const set = (patch: Partial<typeof s>) => setUi({ ...s, ...patch });
  const [notice, setNotice] = useState<{ entity: string; text: string; bad: boolean } | null>(null);
  const { data, error, loading } = useData<Detail>(KNOWN.has(entity) ? `/api/master-data/${entity}` : null);
  const d = data && data.key === entity ? data : null;

  const rows = useMemo(() => {
    if (!d) return [];
    const needle = s.q.trim().toLowerCase();
    if (!needle) return d.rows;
    return d.rows.filter((r) => d.columns.some((c) => cell(r[c.key]).toLowerCase().includes(needle)));
  }, [d, s.q]);

  if (!KNOWN.has(entity)) return <Navigate to="/master/customers" replace />;
  const numeric = (c: Column) => c.kind !== "str";
  const count = d ? (rows.length === d.rows.length ? `${d.rows.length}` : `${rows.length} of ${d.rows.length}`) : "";
  const changed = d ? d.rows.filter((r) => r._status).length + d.deleted.length : 0;
  const done = (text: string, bad = false) => {
    setNotice({ entity, text, bad });
    set({ edit: null, remove: null });
    bump();
  };
  const revert = async (r: Row) => {
    try {
      await post(`/api/master-data/${entity}/${encodeURIComponent(r._key)}/revert`);
      done(`${r._key} is back to the seed.`);
    } catch (e) {
      done(errorText(e), true);
    }
  };
  const shown = notice && notice.entity === entity ? notice : null;

  return (
    <>
      <section className="hero">
        <div>
          <p className="hero-tag">Master Data · {d?.section ?? "reference data"}</p>
          <h1>{d?.label ?? "Master Data"}</h1>
          <p className="hero-sub">
            The plant's reference data every module reads. Changes are kept in this session on top of the seed; Reset returns the seed. Planning, Capacity and
            Material still compute from the seed.
          </p>
        </div>
      </section>
      {shown && <div className={`notice-box ${shown.bad ? "bad" : "good"}`} role={shown.bad ? "alert" : "status"}>{shown.text}</div>}
      <Card
        title={d ? `${d.label} (${count})` : "Master data"}
        actions={
          <div className="row">
            {changed > 0 && <Badge tone="info">{changed} changed in this session</Badge>}
            <input type="search" placeholder="Search" aria-label="Search master data" value={s.q} onChange={(e) => set({ q: e.target.value })} />
            {d?.editable && <button className="primary" onClick={() => set({ edit: "new" })}>+ Add</button>}
          </div>
        }
      >
        <Status loading={loading && !d} error={error} />
        {d && (
          <>
            {!d.editable && <div className="notice-box info">Read-only: {d.label} is derived from other master data. Edit its source instead.</div>}
            {rows.length === 0 ? (
              <p className="muted">{s.q ? `Nothing matches “${s.q}”.` : "No rows."}</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      {d.columns.map((c) => <th key={c.key} className={numeric(c) ? "right" : undefined}>{c.label}</th>)}
                      {d.editable && <th className="right sticky-end">Actions</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r._key}>
                        {d.columns.map((c, i) => (
                          <td key={c.key} className={numeric(c) ? "right" : d.pk.includes(c.key) || c.ref ? "nowrap" : undefined}>
                            {cell(r[c.key])}
                            {i === 0 && r._status && <> <Badge tone={STATUS_TONE[r._status]}>{r._status === "created" ? "Created" : "Edited"}</Badge></>}
                          </td>
                        ))}
                        {d.editable && (
                          <td className="right nowrap sticky-end">
                            <div className="row-actions">
                              {r._status === "edited" && <button className="small ghost" onClick={() => revert(r)} title="Back to the seed values">Revert</button>}
                              <button className="small" onClick={() => set({ edit: r })} aria-label={`Edit ${r._key}`}>Edit</button>
                              <button className="small ghost danger" onClick={() => set({ remove: r })} aria-label={`Delete ${r._key}`}>Delete</button>
                            </div>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {d.deleted.length > 0 && (
              <div className="deleted-rows">
                <h3>Deleted in this session ({d.deleted.length})</h3>
                <ul>
                  {d.deleted.map((r) => (
                    <li key={r._key}>
                      <span><strong>{r._key}</strong> {d.columns.slice(1, 2).map((c) => cell(r[c.key]))}</span>
                      <button className="small" onClick={() => revert(r)}>Restore</button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <Source>{d.source}, with this session's changes on top.</Source>
          </>
        )}
      </Card>
      {d && s.edit && <RowDialog d={d} row={s.edit === "new" ? null : s.edit} onClose={() => set({ edit: null })} onSaved={(t) => done(t)} />}
      {d && s.remove && <DeleteDialog d={d} row={s.remove} onClose={() => set({ remove: null })} onDone={(t) => done(t)} />}
    </>
  );
}
