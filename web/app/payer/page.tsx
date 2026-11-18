import type { Metadata } from "next";
import PayerApp from "@/components/payer/PayerApp";

export const metadata: Metadata = {
  title: "Payer GraphRAG Copilot",
  description: "LangGraph + NetworkX GraphRAG agent for health-plan claims, prior auth, pharmacy and network analytics, using your own Groq key.",
};

export default function Page() {
  return <PayerApp />;
}
