export async function open(path: string): Promise<void> {
  if ("__tauriMockShellFailure" in window) throw new Error("Browser unavailable");
  window.dispatchEvent(new CustomEvent("mock-shell-open", { detail: path }));
}
