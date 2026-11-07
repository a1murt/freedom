import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { ru } from "./ru";
import { en } from "./en";
import { kk } from "./kk";
import { readStorage, writeStorage } from "@/lib/utils";

export const LANGS = ["ru", "en", "kk"] as const;
export type Lang = (typeof LANGS)[number];

const saved = readStorage("cqa.lang");
const initial: Lang = LANGS.includes(saved as Lang) ? (saved as Lang) : "ru";

void i18n.use(initReactI18next).init({
  resources: { ru: { translation: ru }, en: { translation: en }, kk: { translation: kk } },
  lng: initial,
  fallbackLng: "ru",
  interpolation: { escapeValue: false },
  returnNull: false,
});

export function setLang(l: Lang) {
  void i18n.changeLanguage(l);
  writeStorage("cqa.lang", l);
  document.documentElement.lang = l;
}

export const dateLocale = (): string => ({ ru: "ru-RU", en: "en-GB", kk: "kk-KZ" })[i18n.language as Lang] ?? "ru-RU";

export default i18n;
