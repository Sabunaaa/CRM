import { FormEvent, useEffect, useState } from "react";
import { CalendarDays, Check, ChevronLeft, ChevronRight, Circle, CircleAlert, LoaderCircle, Plus, Trash2 } from "lucide-react";
import { api, KpiItem, WeeklyKpiItems } from "./api";
import dachiPfp from "./assets/dachi-pfp.png";

const demoMode = new URLSearchParams(window.location.search).get("demo") === "1";
const managers = ["Dachi", "Lui", "Saba"] as const;

function isoDate(value: Date) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function mondayFor(value = new Date()) {
  const result = new Date(value);
  const weekday = result.getDay() || 7;
  result.setDate(result.getDate() - weekday + 1);
  result.setHours(12, 0, 0, 0);
  return isoDate(result);
}

function shiftWeek(value: string, weeks: number) {
  const result = new Date(`${value}T12:00:00`);
  result.setDate(result.getDate() + weeks * 7);
  return mondayFor(result);
}

function endOfWeek(start: string) {
  const value = new Date(`${start}T12:00:00`);
  value.setDate(value.getDate() + 6);
  return isoDate(value);
}

function formatWeek(start: string, end: string) {
  const formatter = new Intl.DateTimeFormat("en", { timeZone: "Asia/Tbilisi", month: "short", day: "numeric" });
  const startDate = new Date(`${start}T12:00:00+04:00`);
  const endDate = new Date(`${end}T12:00:00+04:00`);
  return `${formatter.format(startDate)} – ${formatter.format(endDate)}, ${endDate.getFullYear()}`;
}

function emptyWeek(week: string, manager: string, items: KpiItem[] = []): WeeklyKpiItems {
  const completed = items.filter(item => item.completed).length;
  return { week_start: week, week_end: endOfWeek(week), manager, items, completed_count: completed, completion_percent: items.length ? Math.round(completed / items.length * 1000) / 10 : null };
}

function ManagerAvatar({ name }: { name: string }) {
  if (name === "Dachi") return <img className="kpi-switch-avatar" src={dachiPfp} alt="Dachi"/>;
  return <span className={`kpi-switch-avatar fallback ${name === "Lui" ? "blue" : "purple"}`}>{name.slice(0, 2).toUpperCase()}</span>;
}

export default function KpiPage({ persona }: { persona: string }) {
  const [week, setWeek] = useState(mondayFor());
  const [manager, setManager] = useState(persona);
  const [data, setData] = useState<WeeklyKpiItems | null>(demoMode ? emptyWeek(mondayFor(), persona) : null);
  const [loading, setLoading] = useState(!demoMode);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [text, setText] = useState("");
  const [error, setError] = useState("");

  useEffect(() => { setManager(persona); }, [persona]);
  useEffect(() => {
    if (demoMode) { setData(current => emptyWeek(week, manager, current?.manager === manager && current.week_start === week ? current.items : [])); return; }
    let cancelled = false;
    setLoading(true);
    setError("");
    api<WeeklyKpiItems>(`/kpi/items?week_start=${week}&manager=${manager}`).then(result => { if (!cancelled) setData(result); }).catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not load KPIs"); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [week, manager]);

  const canEdit = manager === persona;
  async function addItem(event: FormEvent) {
    event.preventDefault();
    const value = text.trim();
    if (!value || !canEdit) return;
    setAdding(true);
    setError("");
    try {
      const item = demoMode
        ? { id: `demo-${Date.now()}`, week_start: week, manager, text: value, completed: false, completed_at: null, created_at: new Date().toISOString() }
        : await api<KpiItem>(`/kpi/items?week_start=${week}`, { method: "POST", body: JSON.stringify({ text: value }) });
      setData(current => emptyWeek(week, manager, [...(current?.items || []), item]));
      setText("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not add KPI"); }
    finally { setAdding(false); }
  }

  async function toggleItem(item: KpiItem) {
    if (!canEdit) return;
    setBusyId(item.id);
    setError("");
    try {
      const updated = demoMode ? { ...item, completed: !item.completed, completed_at: !item.completed ? new Date().toISOString() : null } : await api<KpiItem>(`/kpi/items/${item.id}`, { method: "PUT", body: JSON.stringify({ completed: !item.completed }) });
      setData(current => emptyWeek(week, manager, (current?.items || []).map(row => row.id === item.id ? updated : row)));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not update KPI"); }
    finally { setBusyId(null); }
  }

  async function removeItem(item: KpiItem) {
    if (!canEdit) return;
    setBusyId(item.id);
    setError("");
    try {
      if (!demoMode) await api(`/kpi/items/${item.id}`, { method: "DELETE" });
      setData(current => emptyWeek(week, manager, (current?.items || []).filter(row => row.id !== item.id)));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not delete KPI"); }
    finally { setBusyId(null); }
  }

  return <>
    <section className="page-heading kpi-heading"><div><p className="eyebrow">WEEKLY PERFORMANCE</p><h1>KPI plans</h1><p>Add clear weekly tasks and check them off when they are complete.</p></div><div className="week-picker"><button onClick={() => setWeek(shiftWeek(week, -1))} aria-label="Previous week"><ChevronLeft/></button><div><CalendarDays/><span>{formatWeek(data?.week_start || week, data?.week_end || endOfWeek(week))}</span></div><button onClick={() => setWeek(shiftWeek(week, 1))} aria-label="Next week"><ChevronRight/></button><button className="this-week" onClick={() => setWeek(mondayFor())}>This week</button></div></section>

    <div className="kpi-manager-switch" role="tablist" aria-label="Manager KPI plans">{managers.map(name => <button role="tab" aria-selected={manager === name} className={manager === name ? "active" : ""} onClick={() => setManager(name)} key={name}><ManagerAvatar name={name}/><span><b>{name}</b><small>{name === persona ? "Your KPIs" : "View plan"}</small></span></button>)}</div>

    {error && <div className="notice warning"><CircleAlert/><div><b>KPI update failed</b><span>{error}</span></div></div>}
    {loading || !data ? <div className="page-loader"><LoaderCircle className="spin"/><span>Loading weekly KPIs…</span></div> : <article className="panel kpi-list-card"><header className="kpi-list-header"><div><ManagerAvatar name={manager}/><span><h2>{manager}’s weekly KPIs</h2><p>{data.items.length ? `${data.completed_count} of ${data.items.length} completed` : "No KPIs added for this week"}</p></span></div><strong>{data.completion_percent === null ? "—" : `${Math.round(data.completion_percent)}%`}</strong></header><div className="kpi-overall-progress"><span style={{ width: `${data.completion_percent || 0}%` }}/></div>{canEdit && <form className="kpi-add-form" onSubmit={addItem}><input maxLength={500} value={text} onChange={event => setText(event.target.value)} placeholder="Add a KPI, for example: Contact 10 new creators" aria-label="New KPI"/><button className="primary" disabled={adding || !text.trim()}>{adding ? <LoaderCircle className="spin"/> : <Plus/>}Add KPI</button></form>}{!canEdit && <div className="kpi-readonly">Switch your team profile to {manager} to update this plan.</div>}<div className="kpi-checklist">{data.items.length ? data.items.map(item => <div className={`kpi-check-item ${item.completed ? "completed" : ""}`} key={item.id}><button className="kpi-check" onClick={() => void toggleItem(item)} disabled={!canEdit || busyId === item.id} aria-label={item.completed ? `Mark ${item.text} incomplete` : `Mark ${item.text} complete`}>{busyId === item.id ? <LoaderCircle className="spin"/> : item.completed ? <Check/> : <Circle/>}</button><span>{item.text}</span>{canEdit && <button className="kpi-delete" onClick={() => void removeItem(item)} disabled={busyId === item.id} aria-label={`Delete ${item.text}`}><Trash2/></button>}</div>) : <div className="kpi-empty"><Check/><h3>No KPI items yet</h3><p>{canEdit ? "Add the first task for this week above." : `${manager} has not added any KPI items for this week.`}</p></div>}</div></article>}
  </>;
}
