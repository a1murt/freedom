import { useNavigate, useParams } from "react-router-dom";
import { ConversationCard } from "./ConversationCard";

export default function ConversationCardPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  if (!id) return null;
  return (
    <div className="h-full">
      <ConversationCard id={decodeURIComponent(id)} onBack={() => (window.history.length > 1 ? navigate(-1) : navigate("/conversations"))} />
    </div>
  );
}
