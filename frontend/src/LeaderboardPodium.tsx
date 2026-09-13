import { Crown, Medal } from "lucide-react";
import { Link } from "react-router-dom";

export interface LeaderboardEntry {
  id: string;
  rank: number;
  name: string;
  subtitle: string;
  value: number | null;
  valueLabel: string;
  avatarUrl?: string | null;
  href?: string;
  state?: string;
  observedAt?: string | null;
}

interface LeaderboardPodiumProps {
  entries: LeaderboardEntry[];
  valueTitle: string;
}

function initials(name: string) {
  const letters = name.replace(/^@/, "").split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]);
  return letters.join("").toUpperCase() || "IG";
}

function EntryVisual({ entry }: { entry: LeaderboardEntry }) {
  return entry.avatarUrl ? <img className="leaderboard-avatar" src={entry.avatarUrl} alt="" /> : <span className="leaderboard-avatar fallback">{initials(entry.name)}</span>;
}

function EntryCard({ entry, rank }: { entry: LeaderboardEntry; rank: number }) {
  const content = <>
    <span className="podium-rank">#{rank}</span>
    <EntryVisual entry={entry} />
    <strong>{entry.name}</strong>
    <small>{entry.subtitle}</small>
    <b className="podium-value">{entry.valueLabel}</b>
  </>;

  return entry.href ? <Link className="podium-entry" to={entry.href}>{content}</Link> : <div className="podium-entry">{content}</div>;
}

export function LeaderboardPodium({ entries, valueTitle }: LeaderboardPodiumProps) {
  const byRank = new Map(entries.map(entry => [entry.rank, entry]));
  const slots = [2, 1, 3];

  return <div className="leaderboard-podium" aria-label={`Top three by ${valueTitle}`}>
    {slots.map(rank => {
      const entry = byRank.get(rank);
      return <div className={`podium-slot rank-${rank}`} key={rank}>
        {rank === 1 && <Crown className="podium-crown" aria-hidden="true" />}
        <div className="podium-platform">
          {entry ? <EntryCard entry={entry} rank={rank} /> : <div className="podium-empty"><Medal/><span>Waiting for data</span></div>}
        </div>
      </div>;
    })}
  </div>;
}

export function LeaderboardEntryIcon({ entry }: { entry: LeaderboardEntry }) {
  return entry.avatarUrl ? <img className="leaderboard-row-avatar" src={entry.avatarUrl} alt="" /> : <span className="leaderboard-row-avatar fallback">{initials(entry.name)}</span>;
}
