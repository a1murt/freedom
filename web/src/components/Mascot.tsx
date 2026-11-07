import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import wave from "@/assets/mascot/wave.svg";
import search from "@/assets/mascot/search.svg";
import check from "@/assets/mascot/check.svg";
import shield from "@/assets/mascot/shield.svg";
import sleep from "@/assets/mascot/sleep.svg";
import read from "@/assets/mascot/read.svg";
import oops from "@/assets/mascot/oops.svg";
import typing from "@/assets/mascot/typing.svg";

export type MascotPose = "wave" | "search" | "check" | "shield" | "sleep" | "read" | "oops" | "typing";

const SRC: Record<MascotPose, string> = { wave, search, check, shield, sleep, read, oops, typing };

/**
 * Brand mascot (green octopus from the Figma kit). One per screen at most, never in table rows.
 * `decorative` renders it with an empty alt so screen readers skip it.
 */
export function Mascot({ pose, size = 96, className, decorative = false, title }: { pose: MascotPose; size?: number; className?: string; decorative?: boolean; title?: string }) {
  const { t } = useTranslation();
  const alt = decorative ? "" : (title ?? t(`mascot.${pose}`));
  return <img src={SRC[pose]} width={size} height={size} alt={alt} aria-hidden={decorative || undefined} draggable={false} className={cn("select-none", className)} style={{ width: size, height: size }} />;
}
