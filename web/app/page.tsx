import Hero from "@/components/Hero";
import OptionsSection from "@/components/OptionsSection";
import LanguagesSection from "@/components/LanguagesSection";
import BudgetSection from "@/components/BudgetSection";
import AnalyticsSection from "@/components/AnalyticsSection";
import RoadmapSection from "@/components/RoadmapSection";

export default function Home() {
  return (
    <main>
      <Hero />
      <OptionsSection />
      <LanguagesSection />
      <BudgetSection />
      <AnalyticsSection />
      <RoadmapSection />
    </main>
  );
}
