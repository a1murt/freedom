import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import carrySrc from "@/assets/mascot/carry.svg";
import checkSrc from "@/assets/mascot/check.svg";
import cheerSrc from "@/assets/mascot/cheer.svg";
import dizzySrc from "@/assets/mascot/dizzy.svg";
import downloadSrc from "@/assets/mascot/download.svg";
import flipSrc from "@/assets/mascot/flip.svg";
import globeSrc from "@/assets/mascot/globe.svg";
import juggleSrc from "@/assets/mascot/juggle.svg";
import keySrc from "@/assets/mascot/key.svg";
import mailSrc from "@/assets/mascot/mail.svg";
import oopsSrc from "@/assets/mascot/oops.svg";
import peekSrc from "@/assets/mascot/peek.svg";
import pointSrc from "@/assets/mascot/point.svg";
import readSrc from "@/assets/mascot/read.svg";
import searchSrc from "@/assets/mascot/search.svg";
import shhSrc from "@/assets/mascot/shh.svg";
import shieldSrc from "@/assets/mascot/shield.svg";
import sleepSrc from "@/assets/mascot/sleep.svg";
import swapSrc from "@/assets/mascot/swap.svg";
import tapSrc from "@/assets/mascot/tap.svg";
import tiredSrc from "@/assets/mascot/tired.svg";
import typingSrc from "@/assets/mascot/typing.svg";
import waveSrc from "@/assets/mascot/wave.svg";
import wrenchSrc from "@/assets/mascot/wrench.svg";

/** All 24 poses of the green octopus from the Figma kit (Components → Mascot, recolored to Freedom green). */
export const MASCOT_POSES = ["carry", "check", "cheer", "dizzy", "download", "flip", "globe", "juggle", "key", "mail", "oops", "peek", "point", "read", "search", "shh", "shield", "sleep", "swap", "tap", "tired", "typing", "wave", "wrench"] as const;
export type MascotPose = "carry" | "check" | "cheer" | "dizzy" | "download" | "flip" | "globe" | "juggle" | "key" | "mail" | "oops" | "peek" | "point" | "read" | "search" | "shh" | "shield" | "sleep" | "swap" | "tap" | "tired" | "typing" | "wave" | "wrench";

const SRC: Record<MascotPose, string> = { carry: carrySrc, check: checkSrc, cheer: cheerSrc, dizzy: dizzySrc, download: downloadSrc, flip: flipSrc, globe: globeSrc, juggle: juggleSrc, key: keySrc, mail: mailSrc, oops: oopsSrc, peek: peekSrc, point: pointSrc, read: readSrc, search: searchSrc, shh: shhSrc, shield: shieldSrc, sleep: sleepSrc, swap: swapSrc, tap: tapSrc, tired: tiredSrc, typing: typingSrc, wave: waveSrc, wrench: wrenchSrc };

/**
 * Brand mascot. One per screen at most, never in table rows.
 * `decorative` renders it with an empty alt so screen readers skip it.
 */
export function Mascot({ pose, size = 96, className, decorative = false, title }: { pose: MascotPose; size?: number; className?: string; decorative?: boolean; title?: string }) {
  const { t } = useTranslation();
  const alt = decorative ? "" : (title ?? t(`mascot.${pose}`, { defaultValue: "" }));
  return <img src={SRC[pose]} width={size} height={size} alt={alt} aria-hidden={decorative || undefined} draggable={false} className={cn("select-none", className)} style={{ width: size, height: size }} />;
}
