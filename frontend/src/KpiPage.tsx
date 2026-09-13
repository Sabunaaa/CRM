import { FormEvent, useEffect, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight, CircleAlert, Edit3, LoaderCircle, Save, Target, TrendingUp, X } from "lucide-react";
import { api, KpiMetric, ManagerKpi, WeeklyKpi } from "./api";

const demoMode = new URLSearchParams(window.location.search).get("demo") === "1";
const managers = ["Dachi", "Lui", "Saba"] as const;

interface PlanForm {
  profiles_target: number;
  reels_target: number;
  views_growth_target: number;
  followers_growth_target: number;
  focus: string;
}

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

function formatWeek(start: string, end: string) {
  const formatter = new Intl.DateTimeFormat("en", { timeZone: "Asia/Tbilisi", month: "short", day: "numeric" });
  const startDate = new Date(`${start}T12:00:00+04:00`);
  const endDate = new Date(`${end}T12:00:00+04:00`);
  return `${formatter.format(startDate)} – ${formatter.format(endDate)}, ${endDate.getFullYear()}`;
}

function formatValue(value: number) {
  return Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1, signDisplay: value === 0 ? "never" : "auto" }).format(value);
}

function emptyWeek(week: string): WeeklyKpi {
  const end = shiftWeek(week, 1);
  const endDate = new Date(`${end}T12:00:00`);
  endDate.setDate(endDate.getDate() - 1);
  return {
    week_start: week,
    week_end: isoDate(endDate),
    team_completion_percent: null,
    managers: managers.map(manager => ({ manager, focus: null, completion_percent: null, plan_updated_at: null, metrics: [
      { key: "profiles", label: "Profiles added", actual: 0, target: 0, unit: "profiles", completion_percent: null },
      { key: "reels", label: "Reels discovered", actual: 0, target: 0, unit: "reels", completion_percent: null },
      { key: "views_growth", label: "Reel-view growth", actual: 0, target: 0, unit: "views", completion_percent: null },
      { key: "followers_growth", label: "Follower growth", actual: 0, target: 0, unit: "followers", completion_percent: null },
    ] })),
  };
}

function formFromManager(manager: ManagerKpi): PlanForm {
  const target = (key: KpiMetric["key"]) => manager.metrics.find(metric => metric.key === key)?.target || 0;
  return {
    profiles_target: target("profiles"),
    reels_target: target("reels"),
    views_growth_target: target("views_growth"),
    followers_growth_target: target("followers_growth"),
    focus: manager.focus || "",
  };
}

function ManagerAvatar({ name }: { name: string }) {
  if (name === "Dachi") return <img className="kpi-avatar" src="/dachi-pfp.png" alt="Dachi"/>;
  return <span className={`kpi-avatar fallback ${name === "Lui" ? "blue" : "purple"}`}>{name.slice(0, 2).toUpperCase()}</span>;
}

function KpiRow({ metric }: { metric: KpiMetric }) {
  const progress = Math.min(100, Math.max(0, metric.completion_percent || 0));
  return <div className="kpi-row">
    <div className="kpi-row-copy"><div><b>{metric.label}</b><small>{metric.target > 0 ? `${formatValue(metric.actual)} of ${formatValue(metric.target)} ${metric.unit}` : `${formatValue(metric.actual)} ${metric.unit} · no target`}</small></div><strong>{metric.completion_percent === null ? "—" : `${Math.round(metric.completion_percent)}%`}</strong></div>
    <div className="kpi-progress" aria-label={`${metric.label} progress`}><span style={{ width: `${progress}%` }}/></div>
  </div>;
}

export default function KpiPage({ persona }: { persona: string }) {
  const [week, setWeek] = useState(mondayFor());
  const [data, setData] = useState<WeeklyKpi | null>(demoMode ? emptyWeek(mondayFor()) : null);
  const [loading, setLoading] = useState(!demoMode);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [form, setForm] = useState<PlanForm>({ profiles_target: 0, reels_target: 0, views_growth_target: 0, followers_growth_target: 0, focus: "" });

  useEffect(() => {
    setEditing(false);
    setSaved(false);
    if (demoMode) { setData(emptyWeek(week)); return; }
    let cancelled = false;
    setLoading(true);
    setError("");
    api<WeeklyKpi>(`/kpi/weekly?week_start=${week}`).then(result => { if (!cancelled) setData(result); }).catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not load KPI plans"); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [week]);

  const myPlan = data?.managers.find(manager => manager.manager === persona);

  function editPlan() {
    if (!myPlan) return;
    setForm(formFromManager(myPlan));
    setSaved(false);
    setEditing(true);
  }

  async function savePlan(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      if (demoMode) {
        const next = emptyWeek(week);
        const manager = next.managers.find(item => item.manager === persona)!;
        manager.focus = form.focus || null;
        manager.metrics.forEach(metric => { metric.target = form[`${metric.key}_target` as keyof PlanForm] as number; metric.completion_percent = metric.target ? 0 : null; });
        manager.completion_percent = manager.metrics.some(metric => metric.target > 0) ? 0 : null;
        setData(next);
      } else {
        setData(await api<WeeklyKpi>(`/kpi/weekly?week_start=${week}`, { method: "PUT", body: JSON.stringify(form) }));
      }
      setEditing(false);
      setSaved(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save the weekly plan");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <div className="page-loader"><LoaderCircle className="spin"/><span>Loading weekly KPI plans…</span></div>;
  if (!data) return <section className="panel empty-state"><span><CircleAlert/></span><h2>Could not load KPI plans</h2><p>{error || "Try again shortly."}</p></section>;

  return <>
    <section className="page-heading kpi-heading"><div><p className="eyebrow">WEEKLY PERFORMANCE</p><h1>KPI plans</h1><p>Set weekly goals and compare progress across Dachi, Lui, and Saba.</p></div><div className="week-picker"><button onClick={() => setWeek(shiftWeek(week, -1))} aria-label="Previous week"><ChevronLeft/></button><div><CalendarDays/><span>{formatWeek(data.week_start, data.week_end)}</span></div><button onClick={() => setWeek(shiftWeek(week, 1))} aria-label="Next week"><ChevronRight/></button><button className="this-week" onClick={() => setWeek(mondayFor())}>This week</button></div></section>

    <section className="kpi-team-strip panel"><div className="kpi-team-score"><span><TrendingUp/></span><div><small>Team plan completion</small><strong>{data.team_completion_percent === null ? "No targets yet" : `${Math.round(data.team_completion_percent)}%`}</strong></div></div><p>Growth compares each metric’s first and last available snapshot in this week.</p><button className="primary" onClick={editPlan}><Edit3/> Edit my plan</button></section>

    {error && <div className="notice warning"><CircleAlert/><div><b>KPI update failed</b><span>{error}</span></div></div>}
    {saved && <div className="kpi-saved">Your weekly plan was saved.</div>}

    {editing && <form className="panel kpi-editor" onSubmit={savePlan}><div className="panel-title"><div><h2>{persona}’s plan</h2><p>Targets apply to {formatWeek(data.week_start, data.week_end)}.</p></div><button type="button" className="kpi-close" onClick={() => setEditing(false)} aria-label="Close editor"><X/></button></div><label className="kpi-focus"><span>Weekly focus</span><textarea maxLength={500} value={form.focus} onChange={event => setForm({ ...form, focus: event.target.value })} placeholder="What matters most this week?"/></label><div className="kpi-target-inputs"><label><span>Profiles added</span><input min="0" max="100" type="number" value={form.profiles_target} onChange={event => setForm({ ...form, profiles_target: Number(event.target.value) })}/></label><label><span>Reels discovered</span><input min="0" max="3000" type="number" value={form.reels_target} onChange={event => setForm({ ...form, reels_target: Number(event.target.value) })}/></label><label><span>Reel-view growth</span><input min="0" max="2000000000" type="number" value={form.views_growth_target} onChange={event => setForm({ ...form, views_growth_target: Number(event.target.value) })}/></label><label><span>Follower growth</span><input min="0" max="2000000000" type="number" value={form.followers_growth_target} onChange={event => setForm({ ...form, followers_growth_target: Number(event.target.value) })}/></label></div><div className="kpi-editor-actions"><button type="button" className="secondary-button" onClick={() => setEditing(false)}>Cancel</button><button className="primary" disabled={saving}>{saving ? <LoaderCircle className="spin"/> : <Save/>}{saving ? "Saving…" : "Save plan"}</button></div></form>}

    <section className="kpi-manager-grid">{data.managers.map(manager => <article className={`panel kpi-manager-card ${manager.manager === persona ? "current" : ""}`} key={manager.manager}><header><ManagerAvatar name={manager.manager}/><div><h2>{manager.manager}</h2><span>{manager.manager === persona ? "Your plan" : "Manager"}</span></div><strong className="kpi-score">{manager.completion_percent === null ? "—" : `${Math.round(manager.completion_percent)}%`}</strong></header><p className={`kpi-focus-copy ${manager.focus ? "" : "empty"}`}>{manager.focus || "No weekly focus has been added."}</p><div className="kpi-rows">{manager.metrics.map(metric => <KpiRow metric={metric} key={metric.key}/>)}</div>{manager.manager === persona && <button className="secondary-button kpi-card-edit" onClick={editPlan}><Target/> Set weekly targets</button>}</article>)}</section>
  </>;
}
