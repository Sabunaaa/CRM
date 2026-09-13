import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { BarChart3, CircleAlert, Clock3, FileQuestion, Heart, Search, Trophy, Users } from "lucide-react";
import { api, MetricState, Profile, Reel } from "./api";
import { demoProfiles, demoReels } from "./demo";
import { LeaderboardEntry, LeaderboardEntryIcon, LeaderboardPodium } from "./LeaderboardPodium";

const demoMode = new URLSearchParams(window.location.search).get("demo") === "1";
type Board = "profiles" | "reels" | "managers";
type ReelMetric = "views" | "engagement";
type ManagerMetric = "followers" | "views" | "engagement";

const managerConfig = [
  { name: "Dachi", avatarUrl: "/dachi-pfp.png" },
  { name: "Lui", avatarUrl: null },
  { name: "Saba", avatarUrl: null },
] as const;

interface ManagerSummary {
  name: string;
  avatarUrl: string | null;
  profileCount: number;
  reelCount: number;
  followers: number | null;
  views: number | null;
  engagementTotal: number;
  engagementCount: number;
  observedAt: string | null;
}

function formatMetric(value: number | null) {
  if (value === null) return "—";
  return Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function formatDate(value: string | null) {
  if (!value) return "Not observed";
  return new Intl.DateTimeFormat("en", { timeZone: "Asia/Tbilisi", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function stateLabel(state: MetricState) {
  return state.charAt(0).toUpperCase() + state.slice(1);
}

function profileEntries(profiles: Profile[], search: string): LeaderboardEntry[] {
  const query = search.trim().toLowerCase();
  return profiles
    .filter(profile => !query || profile.username.toLowerCase().includes(query) || (profile.display_name || "").toLowerCase().includes(query))
    .filter(profile => profile.followers_count !== null)
    .sort((a, b) => (b.followers_count ?? -1) - (a.followers_count ?? -1))
    .map((profile, index) => ({
      id: profile.id,
      rank: index + 1,
      name: profile.display_name || `@${profile.username}`,
      subtitle: `@${profile.username}`,
      value: profile.followers_count,
      valueLabel: `${formatMetric(profile.followers_count)} followers`,
      avatarUrl: profile.profile_picture_url,
      href: `/profiles/${profile.id}`,
      state: profile.followers_state,
      observedAt: profile.followers_observed_at || profile.last_collected_at,
    }));
}

function reelEntries(reels: Reel[], search: string, metric: ReelMetric): LeaderboardEntry[] {
  const query = search.trim().toLowerCase();
  return reels
    .filter(reel => !query || (reel.profile_username || "").toLowerCase().includes(query) || (reel.caption || "").toLowerCase().includes(query))
    .filter(reel => (metric === "views" ? reel.views_count : reel.engagement_rate) !== null)
    .sort((a, b) => {
      const aValue = metric === "views" ? a.views_count ?? -1 : a.engagement_rate ?? -1;
      const bValue = metric === "views" ? b.views_count ?? -1 : b.engagement_rate ?? -1;
      return bValue - aValue;
    })
    .map((reel, index) => {
      const value = metric === "views" ? reel.views_count : reel.engagement_rate;
      return {
        id: reel.id,
        rank: index + 1,
        name: reel.caption || "Untitled reel",
        subtitle: `@${reel.profile_username || "unknown"}`,
        value,
        valueLabel: metric === "views" ? `${formatMetric(value)} views` : `${value}% engagement`,
        avatarUrl: reel.thumbnail_url,
        href: `/reels/${reel.id}`,
        state: reel.metrics_state,
        observedAt: reel.metrics_observed_at,
      };
    });
}

function latestDate(current: string | null, next: string | null) {
  if (!current) return next;
  if (!next) return current;
  return new Date(next).getTime() > new Date(current).getTime() ? next : current;
}

function managerSummaries(profiles: Profile[], reels: Reel[]): ManagerSummary[] {
  const summaries = new Map<string, ManagerSummary>(managerConfig.map(manager => [manager.name.toLowerCase(), {
    name: manager.name,
    avatarUrl: manager.avatarUrl,
    profileCount: 0,
    reelCount: 0,
    followers: null,
    views: null,
    engagementTotal: 0,
    engagementCount: 0,
    observedAt: null,
  } satisfies ManagerSummary]));
  const profileManagers = new Map<string, ManagerSummary>();
  const usernameManagers = new Map<string, ManagerSummary>();

  profiles.forEach(profile => {
    const manager = summaries.get(profile.added_by.toLowerCase());
    if (!manager) return;
    manager.profileCount += 1;
    if (profile.followers_count !== null) manager.followers = (manager.followers ?? 0) + profile.followers_count;
    manager.observedAt = latestDate(manager.observedAt, profile.followers_observed_at || profile.last_collected_at);
    profileManagers.set(profile.id, manager);
    usernameManagers.set(profile.username.toLowerCase(), manager);
  });

  reels.forEach(reel => {
    const manager = profileManagers.get(reel.profile_id) || (reel.profile_username ? usernameManagers.get(reel.profile_username.toLowerCase()) : undefined);
    if (!manager) return;
    manager.reelCount += 1;
    if (reel.views_count !== null) manager.views = (manager.views ?? 0) + reel.views_count;
    if (reel.engagement_rate !== null) {
      manager.engagementTotal += reel.engagement_rate;
      manager.engagementCount += 1;
    }
    manager.observedAt = latestDate(manager.observedAt, reel.metrics_observed_at);
  });

  return [...summaries.values()];
}

function managerEntries(profiles: Profile[], reels: Reel[], search: string, metric: ManagerMetric): LeaderboardEntry[] {
  const query = search.trim().toLowerCase();
  return managerSummaries(profiles, reels)
    .filter(manager => !query || manager.name.toLowerCase().includes(query))
    .sort((a, b) => {
      const value = (manager: ManagerSummary) => metric === "followers" ? manager.followers : metric === "views" ? manager.views : manager.engagementCount ? manager.engagementTotal / manager.engagementCount : null;
      return (value(b) ?? -1) - (value(a) ?? -1);
    })
    .map((manager, index) => {
      const value = metric === "followers" ? manager.followers : metric === "views" ? manager.views : manager.engagementCount ? manager.engagementTotal / manager.engagementCount : null;
      const valueLabel = value === null ? "No data" : metric === "followers" ? `${formatMetric(value)} followers` : metric === "views" ? `${formatMetric(value)} views` : `${value.toFixed(2)}% engagement`;
      return {
        id: `manager-${manager.name.toLowerCase()}`,
        rank: index + 1,
        name: manager.name,
        subtitle: `${manager.profileCount} profile${manager.profileCount === 1 ? "" : "s"} · ${manager.reelCount} reel${manager.reelCount === 1 ? "" : "s"}`,
        value,
        valueLabel,
        avatarUrl: manager.avatarUrl,
        state: value === null ? "unavailable" : "available",
        observedAt: manager.observedAt,
      };
    });
}

async function loadAllReels(sort: ReelMetric): Promise<Reel[]> {
  const items: Reel[] = [];
  let offset = 0;
  let total = 0;
  do {
    const page = await api<{ items: Reel[]; total: number }>(`/reels?sort=${sort}&limit=100&offset=${offset}`);
    items.push(...page.items);
    total = page.total;
    offset += page.items.length;
    if (!page.items.length) break;
  } while (offset < total);
  return items;
}

function StatusPill({ state }: { state: MetricState }) {
  return <span className={`status ${state}`}><i/>{stateLabel(state)}</span>;
}

export default function LeaderboardPage() {
  const [board, setBoard] = useState<Board>("managers");
  const [reelMetric, setReelMetric] = useState<ReelMetric>("views");
  const [managerMetric, setManagerMetric] = useState<ManagerMetric>("followers");
  const [search, setSearch] = useState("");
  const [profiles, setProfiles] = useState<Profile[]>(demoMode ? demoProfiles : []);
  const [reels, setReels] = useState<Reel[]>(demoMode ? demoReels : []);
  const [loading, setLoading] = useState(!demoMode);
  const [error, setError] = useState("");

  useEffect(() => {
    if (demoMode) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    Promise.all([
      api<{ items: Profile[] }>("/profiles?sort=followers&limit=100"),
      loadAllReels(reelMetric),
    ]).then(([profileResponse, reelResponse]) => {
      if (cancelled) return;
      setProfiles(profileResponse.items);
      setReels(reelResponse);
    }).catch(reason => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not load leaderboard data");
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [reelMetric]);

  const entries = useMemo(() => board === "profiles" ? profileEntries(profiles, search) : board === "reels" ? reelEntries(reels, search, reelMetric) : managerEntries(profiles, reels, search, managerMetric), [board, profiles, reels, search, reelMetric, managerMetric]);
  const valueTitle = board === "profiles" ? "followers" : board === "reels" ? reelMetric === "views" ? "views" : "engagement" : managerMetric === "followers" ? "followers" : managerMetric === "views" ? "reel views" : "engagement";
  const boardLabel = board === "profiles" ? "profiles" : board === "reels" ? "reels" : "managers";
  const summaryText = board === "managers" ? `Showing all ${entries.length} managers; totals use available metrics.` : `Showing ${Math.min(entries.length, 100)} ranked ${boardLabel} with available metrics.`;

  if (loading) return <div className="page-loader"><Trophy className="spin"/><span>Loading leaderboard…</span></div>;
  if (error) return <section className="panel empty-state"><span><CircleAlert/></span><h2>Could not load leaderboard</h2><p>{error}</p></section>;

  return <>
    <section className="page-heading leaderboard-heading"><div><p className="eyebrow">RANKINGS</p><h1>Leaderboard</h1><p>Compare tracked profiles, reels, and the managers responsible for them.</p></div><div className="leaderboard-updated"><Clock3/><span>Latest observed values</span></div></section>
    <section className="leaderboard-controls">
      <div className="leaderboard-tabs" role="tablist" aria-label="Leaderboard type">
        <button className={board === "profiles" ? "active" : ""} onClick={() => setBoard("profiles")} role="tab" aria-selected={board === "profiles"}><Users/> Profiles</button>
        <button className={board === "reels" ? "active" : ""} onClick={() => setBoard("reels")} role="tab" aria-selected={board === "reels"}><BarChart3/> Reels</button>
        <button className={board === "managers" ? "active" : ""} onClick={() => setBoard("managers")} role="tab" aria-selected={board === "managers"}><Trophy/> Managers</button>
      </div>
      <label className="search-box leaderboard-search"><Search/><input value={search} onChange={event => setSearch(event.target.value)} placeholder={board === "profiles" ? "Search profiles…" : board === "reels" ? "Search reels or creators…" : "Search managers…"}/></label>
      {board === "reels" && <label className="leaderboard-select"><span>Rank by</span><select value={reelMetric} onChange={event => setReelMetric(event.target.value as ReelMetric)}><option value="views">Views</option><option value="engagement">Engagement</option></select></label>}
      {board === "managers" && <label className="leaderboard-select"><span>Rank by</span><select value={managerMetric} onChange={event => setManagerMetric(event.target.value as ManagerMetric)}><option value="followers">Followers</option><option value="views">Reel views</option><option value="engagement">Avg engagement</option></select></label>}
    </section>
    <section className="panel leaderboard-card">
      <div className="panel-title"><div><h2>Top performers</h2><p>{summaryText}</p></div><span className="formula-tag">{valueTitle}</span></div>
      {entries.length ? <LeaderboardPodium entries={entries.slice(0, 3)} valueTitle={valueTitle}/> : <div className="leaderboard-empty"><FileQuestion/><h3>No ranked data yet</h3><p>Add a profile or wait for a successful collection to populate this board.</p></div>}
    </section>
    {entries.length > 0 && <section className="panel leaderboard-table-card"><div className="panel-title"><div><h2>All rankings</h2><p>Unavailable and missing values are excluded from metric totals.</p></div></div><div className="table-scroll"><table><thead><tr><th>Rank</th><th>{board === "profiles" ? "Profile" : board === "reels" ? "Reel" : "Manager"}</th><th>{valueTitle}</th><th>Last observed</th><th>Status</th></tr></thead><tbody>{entries.map(entry => <tr key={entry.id}><td className="leaderboard-rank-cell">#{entry.rank}</td><td>{entry.href ? <Link className="leaderboard-row-link" to={entry.href}><LeaderboardEntryIcon entry={entry}/><div><b>{entry.name}</b><small>{entry.subtitle}</small></div></Link> : <div className="leaderboard-row-link"><LeaderboardEntryIcon entry={entry}/><div><b>{entry.name}</b><small>{entry.subtitle}</small></div></div>}</td><td>{entry.valueLabel}</td><td>{formatDate(entry.observedAt || null)}</td><td><StatusPill state={(entry.state || "unavailable") as MetricState}/></td></tr>)}</tbody></table></div></section>}
    <p className="leaderboard-note"><Heart/> {board === "managers" ? "Managers are ranked from the profiles they added. Followers and reel views are totals; engagement is the average of available reel rates." : "Metrics come from the latest successful public collection. Reel engagement uses (likes + comments) ÷ followers × 100 when all inputs are available."}</p>
  </>;
}
