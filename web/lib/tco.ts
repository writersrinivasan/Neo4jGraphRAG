// Illustrative 3-year TCO model. Every assumption is visible in the UI and should be
// replaced with the client's real infra rates and vendor quotes before a decision.

export interface TcoInputs {
  elementsM: number;      // graph size: millions of nodes + edges
  years: number;
  fteCost: number;        // fully-loaded engineer cost / year (USD)
  ramGbMonth: number;     // infra cost per GB of RAM per month (servers incl. storage)
  devFte: number;         // baseline FTE/year building graph features (fraud, GraphRAG)
  needHA: boolean;
}

export interface TcoProfile {
  id: string;
  name: string;
  bytesPerElem: number;   // RAM needed per node+edge (working set)
  servers: number;        // servers without HA
  haServers: number | null; // servers with HA, null = no HA possible in this edition
  licensePerServerYr: number;
  opsFte: number;
  devMultiplier: number;  // effort to build graph features vs. a native Cypher engine (1.0)
  migrationFteMonths: number;
  maxElementsM: number;   // practical ceiling
  inHouse: boolean;
  note: string;
}

export const PROFILES: TcoProfile[] = [
  { id: "mysql", name: "MySQL only (status quo)", bytesPerElem: 60, servers: 1, haServers: 2, licensePerServerYr: 0, opsFte: 0.15, devMultiplier: 2.5, migrationFteMonths: 0, maxElementsM: 5000, inHouse: true,
    note: "No migration, but every multi-hop / fraud feature is hand-written SQL (dev ×2.5)." },
  { id: "networkx", name: "NetworkX (in-process)", bytesPerElem: 379, servers: 1, haServers: null, licensePerServerYr: 0, opsFte: 0.1, devMultiplier: 1.3, migrationFteMonths: 1, maxElementsM: 300, inHouse: true,
    note: "379 B/element measured in this repo. No HA, no concurrency: batch analytics and pilots only." },
  { id: "age", name: "PostgreSQL + AGE + pgvector", bytesPerElem: 80, servers: 1, haServers: 3, licensePerServerYr: 0, opsFte: 0.25, devMultiplier: 1.15, migrationFteMonths: 2, maxElementsM: 2000, inHouse: true,
    note: "Graph + vector + relational in one open-source DB; Patroni for HA; reuses SQL DBA skills." },
  { id: "neo4jc", name: "Neo4j Community", bytesPerElem: 120, servers: 1, haServers: null, licensePerServerYr: 0, opsFte: 0.25, devMultiplier: 1.0, migrationFteMonths: 3, maxElementsM: 5000, inHouse: true,
    note: "Best tooling; single server only, no RBAC. Fine for pilot, weak for PHI production." },
  { id: "memgraph", name: "Memgraph Community", bytesPerElem: 200, servers: 1, haServers: null, licensePerServerYr: 0, opsFte: 0.25, devMultiplier: 1.0, migrationFteMonths: 3, maxElementsM: 1500, inHouse: true,
    note: "In-memory speed for real-time scoring; HA/RBAC need Enterprise." },
  { id: "nebula", name: "NebulaGraph (OSS, distributed)", bytesPerElem: 100, servers: 3, haServers: 3, licensePerServerYr: 0, opsFte: 0.6, devMultiplier: 1.1, migrationFteMonths: 4, maxElementsM: 100000, inHouse: true,
    note: "Free and HA-capable but ops-heavy; pays off only at billions of edges." },
  { id: "neo4je", name: "Neo4j Enterprise", bytesPerElem: 120, servers: 1, haServers: 3, licensePerServerYr: 40000, opsFte: 0.3, devMultiplier: 1.0, migrationFteMonths: 3, maxElementsM: 100000, inHouse: true,
    note: "ASSUMED $40k/server/yr for modelling. Real pricing is quote-based; replace with the actual quote." },
  { id: "managed", name: "Managed cloud graph (reference)", bytesPerElem: 120, servers: 1, haServers: 2, licensePerServerYr: 0, opsFte: 0.05, devMultiplier: 1.0, migrationFteMonths: 3, maxElementsM: 100000, inHouse: false,
    note: "Assumes ~2.5× infra premium vs self-hosted; PHI leaves premises (BAA required)." },
];

export interface TcoResult {
  profile: TcoProfile;
  infra: number; license: number; ops: number; dev: number; migration: number; total: number;
  ramGbPerServer: number; servers: number;
  feasible: boolean; reasons: string[];
}

export function computeTco(p: TcoProfile, i: TcoInputs): TcoResult {
  const reasons: string[] = [];
  const servers = i.needHA ? (p.haServers ?? p.servers) : p.servers;
  if (i.needHA && p.haServers === null) reasons.push("No HA in this edition");
  if (i.elementsM > p.maxElementsM) reasons.push(`Beyond practical size (~${p.maxElementsM}M)`);
  if (!p.inHouse) reasons.push("Not in-house (PHI off-premises)");

  // Distributed engines shard the working set; others replicate it on every node.
  const sharded = p.id === "nebula";
  const totalGb = (i.elementsM * 1e6 * p.bytesPerElem) / 1e9 * 1.5; // 50% headroom
  const ramGbPerServer = Math.max(16, Math.ceil(sharded ? totalGb / servers : totalGb));
  const premium = p.id === "managed" ? 2.5 : 1;
  const infra = servers * ramGbPerServer * i.ramGbMonth * 12 * i.years * premium;
  const license = servers * p.licensePerServerYr * i.years;
  const ops = p.opsFte * i.fteCost * i.years;
  const dev = i.devFte * p.devMultiplier * i.fteCost * i.years;
  const migration = (p.migrationFteMonths / 12) * i.fteCost;
  const total = infra + license + ops + dev + migration;
  return { profile: p, infra, license, ops, dev, migration, total, ramGbPerServer, servers, feasible: reasons.length === 0, reasons };
}

export const DEFAULT_INPUTS: TcoInputs = {
  elementsM: 50, years: 3, fteCost: 120000, ramGbMonth: 4, devFte: 1, needHA: true,
};

export const usd = (n: number) =>
  n >= 1e6 ? `$${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `$${Math.round(n / 1e3)}k` : `$${Math.round(n)}`;
