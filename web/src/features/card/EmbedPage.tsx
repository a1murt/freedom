import { useTranslation } from "react-i18next";
import { ConversationCard } from "./ConversationCard";
import { EmptyState } from "@/components/common";

/** `?embed=1&conversation=<id>` renders only the card (Dynamics 365 form tab). */
export default function EmbedPage({ conversationId }: { conversationId: string | null }) {
  const { t } = useTranslation();
  if (!conversationId) {
    return (
      <div className="p-6">
        <EmptyState title={t("embed.title")} hint={t("embed.missing")} />
      </div>
    );
  }
  return (
    <div className="h-full bg-bg">
      <ConversationCard id={conversationId} embed />
    </div>
  );
}
