export type View =
  | "home"
  | "library"
  | "reader"
  | "daily"
  | "settings"
  | "tasks"
  | "analysis";

/** Old paper links still open their library detail; the bare root is home. */
export function workspaceRoute(search: string) {
  const params = new URLSearchParams(search);
  const requested = params.get("view") || "";
  const views: View[] = [
    "home",
    "library",
    "reader",
    "daily",
    "settings",
    "tasks",
    "analysis",
  ];
  const paper = params.get("paper") || "";
  const view: View = views.includes(requested as View)
    ? (requested as View)
    : paper
      ? "library"
      : "home";
  return {
    view,
    paper: view === "home" ? "" : paper,
    translated: params.get("document") === "translated",
  };
}
