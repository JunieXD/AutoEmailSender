import { Link, NavLink } from "react-router-dom";
import clsx from "clsx";
import { BrainCircuit, Mail, UserCircle2 } from "lucide-react";
import { DesktopUpdateButton } from "@/components/molecules/DesktopUpdateButton";
import { TopBarSelectMenu } from "@/components/atoms/TopBarSelectMenu";
import { useSelectionContext } from "@/context/SelectionContext";

export const TopNavBar = () => {
  const {
    identities,
    llmProfiles,
    selectedIdentityId,
    selectedLlmProfileId,
    setSelectedIdentityId,
    setSelectedLlmProfileId,
    loading,
  } = useSelectionContext();

  const navItems = [
    { label: "首页", href: "/" },
    { label: "导师管理", href: "/professors" },
    { label: "任务中心", href: "/tasks" },
    { label: "统计面板", href: "/dashboard" },
    { label: "个人中心", href: "/profile" },
  ];

  const identityOptions = identities.map((identity) => {
    const profileName = identity.profile_name || identity.name;
    return {
      value: identity.id,
      label: `${profileName}${identity.is_default ? "（默认）" : ""}`,
    };
  });
  const llmOptions = llmProfiles.map((profile) => ({
    value: profile.id,
    label: `${profile.name}${profile.is_default ? "（默认）" : ""}`,
  }));

  return (
    <nav
      data-app-header="true"
      className="sticky top-0 z-50 border-b border-stone-200/80 bg-[linear-gradient(180deg,rgba(255,250,241,0.98),rgba(255,247,237,0.94))] shadow-[0_10px_30px_-24px_rgba(41,37,36,0.4)] backdrop-blur-xl"
    >
      <div className="mx-auto max-w-7xl px-6 py-3">
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
            <Link to="/" className="flex items-center gap-3">
              <div className="rounded-2xl bg-primary p-2.5 text-white shadow-sm shadow-primary/20">
                <Mail className="h-5 w-5" />
              </div>
              <div>
                <div className="text-base font-semibold tracking-[0.01em] text-stone-900">
                  Auto Email Sender
                </div>
                <div className="text-xs text-stone-500">自动套磁系统</div>
              </div>
            </Link>

            <div className="flex flex-wrap items-center justify-end gap-3">
              <DesktopUpdateButton />

              <TopBarSelectMenu
                placeholder="身份"
                icon={<UserCircle2 className="h-4 w-4" />}
                value={selectedIdentityId ?? ""}
                disabled={loading || identities.length === 0}
                options={
                  identityOptions.length > 0
                    ? identityOptions
                    : [{ value: "", label: "请选择身份" }]
                }
                onChange={(nextValue) => {
                  const value = nextValue ? Number(nextValue) : null;
                  setSelectedIdentityId(value);
                }}
              />

              <TopBarSelectMenu
                placeholder="模型"
                icon={<BrainCircuit className="h-4 w-4" />}
                value={selectedLlmProfileId ?? ""}
                disabled={loading || llmProfiles.length === 0}
                options={
                  llmOptions.length > 0
                    ? llmOptions
                    : [{ value: "", label: "请选择模型" }]
                }
                onChange={(nextValue) => {
                  const value = nextValue ? Number(nextValue) : null;
                  setSelectedLlmProfileId(value);
                }}
              />
            </div>
          </div>

          <div className="flex flex-nowrap items-center gap-3 overflow-x-auto rounded-2xl border border-stone-200/80 bg-white/92 p-1.5 shadow-sm shadow-stone-200/50">
            {navItems.map((item) => (
              <NavLink
                key={item.href}
                to={item.href}
                className={({ isActive }) =>
                  clsx(
                    "inline-flex min-w-20 shrink-0 items-center justify-center rounded-xl border px-4 py-1 text-sm font-medium whitespace-nowrap transition-all",
                    isActive
                      ? "border-primary/15 bg-primary text-white shadow-sm shadow-primary/25"
                      : "border-transparent bg-transparent text-stone-600 hover:border-stone-200 hover:bg-stone-50 hover:text-stone-900",
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </div>
        </div>
      </div>
    </nav>
  );
};
