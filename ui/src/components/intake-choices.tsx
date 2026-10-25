import type { Decisions, Preview, ImportProfile, Addition } from "@/api/types";
import { Differences } from "@/components/differences";
import { placeOf } from "@/components/mentor";
import { Button } from "@/components/ui/button";

export function IntakeChoices({
  preview,
  decisions,
  onChange,
  source = "表格",
}: {
  preview: Preview;
  decisions: Decisions;
  onChange: (next: Decisions | ((previous: Decisions) => Decisions)) => void;
  source?: string;
}) {
  const choose = onChange;
  return (
    <>
      <IntakeAdditions additions={preview.additions} />
      {preview.differences.length > 0 && (
        <section>
          <h3 className="mb-2 font-medium">资料不同</h3>
          <Differences
            source={source}
            items={preview.differences}
            take={decisions.take}
            onChange={(take) => choose((d) => ({ ...d, take }))}
          />
        </section>
      )}
      {preview.maybeSame.length > 0 && (
        <section>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h3 className="mr-auto font-medium">可能是同一位 · {preview.maybeSame.length} 组</h3>
            <Button variant="ghost" size="sm" onClick={() => choose((d) => ({ ...d, matches: [] }))}>
              全部按默认
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                choose((d) => ({ ...d, matches: preview.maybeSame.map((m) => ({ row: m.row, id: null })) }))
              }
            >
              全部新建
            </Button>
          </div>
          <div className="space-y-2">
            {preview.maybeSame.map((m) => (
              <div key={m.row} className="rounded-lg border border-border p-3 text-sm">
                <div className="grid grid-cols-2 gap-4 phone:grid-cols-1">
                  <div>
                    <span className="mb-1 block text-xs text-muted-foreground">
                      {source} · 第 {m.rows.join("、")} 项
                    </span>
                    <ProfileDetails profile={m.incoming} />
                  </div>
                  <fieldset className="min-w-0 space-y-3">
                    <legend className="mb-1 text-xs text-muted-foreground">选择并入哪一位，或新建</legend>
                    {m.candidates.map((candidate) => (
                      <label key={candidate.id} className="flex items-start gap-2">
                        <input
                          type="radio"
                          name={`same-${m.row}`}
                          className="mt-1"
                          checked={m.selected === candidate.id}
                          onChange={() =>
                            choose((d) => ({
                              ...d,
                              matches: [...d.matches.filter((c) => c.row !== m.row), { row: m.row, id: candidate.id }],
                            }))
                          }
                        />
                        <span className="min-w-0">
                          <span className="mb-1 block text-xs text-muted-foreground">并入 · 编号 {candidate.id}</span>
                          <ProfileDetails profile={candidate.profile} />
                        </span>
                      </label>
                    ))}
                    <label className="flex items-center gap-2">
                      <input
                        type="radio"
                        name={`same-${m.row}`}
                        checked={m.selected === null}
                        onChange={() =>
                          choose((d) => ({
                            ...d,
                            matches: [...d.matches.filter((c) => c.row !== m.row), { row: m.row, id: null }],
                          }))
                        }
                      />
                      新建
                    </label>
                  </fieldset>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
      {preview.trashed.length > 0 && (
        <section>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h3 className="mr-auto font-medium">在回收站里 · {preview.trashed.length} 位</h3>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => choose((d) => ({ ...d, restore: preview.trashed.map((m) => m.id) }))}
            >
              全部恢复并更新
            </Button>
            <Button variant="ghost" size="sm" onClick={() => choose((d) => ({ ...d, restore: [] }))}>
              全部跳过
            </Button>
          </div>
          <div className="space-y-2">
            {preview.trashed.map((m) => (
              <div key={m.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-border p-3 text-sm">
                <p className="mr-auto">
                  {m.name} · 第 {m.rows.join("、")} 项
                </p>
                {[false, true].map((restore) => (
                  <label key={String(restore)} className="flex items-center gap-2">
                    <input
                      type="radio"
                      name={`restore-${m.id}`}
                      checked={decisions.restore.includes(m.id) === restore}
                      onChange={() =>
                        choose((d) => ({
                          ...d,
                          restore: [...d.restore.filter((id) => id !== m.id), ...(restore ? [m.id] : [])],
                        }))
                      }
                    />
                    {restore ? "恢复并更新" : "跳过"}
                  </label>
                ))}
              </div>
            ))}
          </div>
        </section>
      )}
    </>
  );
}

export function ProfileDetails({ profile }: { profile: ImportProfile }) {
  return (
    <span className="block break-words">
      {profile.name}
      {profile.aliases.length > 0 && <span className="ml-2 text-muted-foreground">{profile.aliases.join("、")}</span>}
      <span className="block text-muted-foreground">
        {profile.affiliations.map((a, n) => (
          <span key={n} className="block">
            {placeOf(a)}
            {a.title && ` · ${a.title}`}
          </span>
        ))}
        <span className="block">{profile.emails.join("、") || "未填写邮箱"}</span>
        {profile.research && <span className="block whitespace-pre-wrap">{profile.research}</span>}
        {profile.homepages.map((url) => (
          <span key={url} className="block">
            {url}
          </span>
        ))}
      </span>
    </span>
  );
}

export function IntakeAdditions({ additions }: { additions: Addition[] }) {
  if (additions.length === 0) return null;
  return (
    <section className="space-y-3">
      <h3 className="font-medium">补充内容</h3>
      {additions.map((a) => {
        const items: [string, string[]][] = [
          ["邮箱", a.fields.emails],
          ["别名", a.fields.aliases],
          [
            "单位",
            a.fields.affiliations.map(({ before, added }) => {
              const fields = [
                ["学校", added.university],
                ["学院", added.school],
                ["系所", added.department],
                ["职称", added.title],
              ]
                .filter(([, value]) => value)
                .map(([label, value]) => `${label}：${value}`)
                .join("；");
              return before
                ? `现有单位（${[placeOf(before), before.title].filter(Boolean).join(" · ")}）补上${fields}`
                : `新增单位 · ${fields}`;
            }),
          ],
          ["主页", a.fields.homepages],
          ["研究方向", a.fields.research ? [a.fields.research] : []],
          ["代表论文", a.papers],
          ["标签", a.tags],
          ["来源网址", a.sourceUrl ? [a.sourceUrl] : []],
          ["备注", a.note ? [a.note] : []],
        ];
        return (
          <div key={a.target.id} className="space-y-2 rounded-lg border border-border p-3 text-sm">
            <p>
              补给 <strong>{a.target.profile.name}</strong> · 编号 {a.target.id}
            </p>
            {a.target.profile.affiliations.length > 0 && (
              <p className="text-muted-foreground">{a.target.profile.affiliations.map(placeOf).join("；")}</p>
            )}
            {items.some(([, values]) => values.length > 0) ? (
              <dl className="space-y-2">
                {items
                  .filter(([, values]) => values.length > 0)
                  .map(([label, values]) => (
                    <div key={label}>
                      <dt className="text-xs text-muted-foreground">补上{label}</dt>
                      <dd className="break-words whitespace-pre-wrap">{values.join("\n")}</dd>
                    </div>
                  ))}
              </dl>
            ) : (
              <p className="text-muted-foreground">没有可直接补上的空项；资料不同的部分由你选择</p>
            )}
          </div>
        );
      })}
    </section>
  );
}
