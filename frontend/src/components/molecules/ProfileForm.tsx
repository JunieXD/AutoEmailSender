import { useState } from 'react';
import type { ImapConfig, Profile, ResumeFile, SmtpConfig } from '@/types';
import { SelectionToggleButton } from '@/components/molecules/SelectionToggleButton';

interface ProfileFormProps {
  initialProfile?: Profile;
  isNew?: boolean;
  errors: Record<string, string>;
  onSave: (data: Omit<Profile, 'id' | 'createdAt' | 'updatedAt'>) => void;
  onCancel: () => void;
}

const defaultSmtp = (): SmtpConfig => ({
  host: '',
  port: 587,
  username: '',
  password: '',
  fromEmail: '',
  fromName: '',
  useTLS: true,
});

const defaultImap = (): ImapConfig => ({
  host: '',
  port: 993,
  username: '',
  password: '',
  useSSL: true,
});

export const ProfileForm: React.FC<ProfileFormProps> = ({
  initialProfile,
  isNew,
  errors,
  onSave,
  onCancel,
}) => {
  const [name, setName] = useState(initialProfile?.name ?? '');
  const [title, setTitle] = useState(initialProfile?.title ?? '');
  const [direction, setDirection] = useState(initialProfile?.direction ?? '');
  const [avatar, setAvatar] = useState(initialProfile?.avatar ?? '');
  const [smtp, setSmtp] = useState<SmtpConfig>(initialProfile?.smtp ?? defaultSmtp());
  const [imap, setImap] = useState<ImapConfig | undefined>(initialProfile?.imap ?? defaultImap());
  const [resumes, setResumes] = useState<ResumeFile[]>(initialProfile?.resumes ?? []);
  const [isDefault, setIsDefault] = useState(initialProfile?.isDefault ?? false);

  const handleAddResume = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    const nextResumes = files.map((file) => ({
      name: file.name,
      size: file.size,
      url: URL.createObjectURL(file),
    }));
    setResumes((previous) => [...previous, ...nextResumes]);
    event.target.value = '';
  };

  const handleSave = () => {
    onSave({
      name,
      title,
      direction,
      avatar,
      smtp,
      imap,
      resumes,
      isDefault,
    });
  };

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <label className="block">
          <div className="mb-2 text-sm font-medium text-stone-800">身份名称</div>
          <input value={name} onChange={(event) => setName(event.target.value)} className="form-input" />
          {errors.name && <p className="mt-1 text-xs text-red-500">{errors.name}</p>}
        </label>
        <label className="block">
          <div className="mb-2 text-sm font-medium text-stone-800">身份描述</div>
          <input value={title} onChange={(event) => setTitle(event.target.value)} className="form-input" />
        </label>
      </div>

      <label className="block">
        <div className="mb-2 text-sm font-medium text-stone-800">研究方向</div>
        <input
          value={direction}
          onChange={(event) => setDirection(event.target.value)}
          className="form-input"
        />
        {errors.direction && <p className="mt-1 text-xs text-red-500">{errors.direction}</p>}
      </label>

      <label className="block">
        <div className="mb-2 text-sm font-medium text-stone-800">头像 URL</div>
        <input value={avatar} onChange={(event) => setAvatar(event.target.value)} className="form-input" />
      </label>

      <div className="grid gap-4 md:grid-cols-2">
        <label className="block">
          <div className="mb-2 text-sm font-medium text-stone-800">SMTP Host</div>
          <input
            value={smtp.host}
            onChange={(event) => setSmtp((previous) => ({ ...previous, host: event.target.value }))}
            className="form-input"
          />
        </label>
        <label className="block">
          <div className="mb-2 text-sm font-medium text-stone-800">SMTP Port</div>
          <input
            type="number"
            value={smtp.port}
            onChange={(event) =>
              setSmtp((previous) => ({ ...previous, port: Number(event.target.value || '0') }))
            }
            className="form-input"
          />
        </label>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <label className="block">
          <div className="mb-2 text-sm font-medium text-stone-800">SMTP 用户名</div>
          <input
            value={smtp.username}
            onChange={(event) => setSmtp((previous) => ({ ...previous, username: event.target.value }))}
            className="form-input"
          />
        </label>
        <label className="block">
          <div className="mb-2 text-sm font-medium text-stone-800">SMTP 密码</div>
          <input
            type="password"
            value={smtp.password}
            onChange={(event) => setSmtp((previous) => ({ ...previous, password: event.target.value }))}
            className="form-input"
          />
        </label>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <label className="block">
          <div className="mb-2 text-sm font-medium text-stone-800">发件人姓名</div>
          <input
            value={smtp.fromName}
            onChange={(event) => setSmtp((previous) => ({ ...previous, fromName: event.target.value }))}
            className="form-input"
          />
        </label>
        <label className="block">
          <div className="mb-2 text-sm font-medium text-stone-800">发件人邮箱</div>
          <input
            type="email"
            value={smtp.fromEmail}
            onChange={(event) => setSmtp((previous) => ({ ...previous, fromEmail: event.target.value }))}
            className="form-input"
          />
        </label>
      </div>

      <details className="rounded-2xl border border-stone-200 bg-stone-50 p-4">
        <summary className="cursor-pointer text-sm font-medium text-stone-800">高级配置（IMAP / 简历）</summary>
        <div className="mt-4 space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block">
              <div className="mb-2 text-sm font-medium text-stone-800">IMAP Host</div>
              <input
                value={imap?.host ?? ''}
                onChange={(event) =>
                  setImap((previous) => ({ ...(previous ?? defaultImap()), host: event.target.value }))
                }
                className="form-input"
              />
            </label>
            <label className="block">
              <div className="mb-2 text-sm font-medium text-stone-800">IMAP Port</div>
              <input
                type="number"
                value={imap?.port ?? 993}
                onChange={(event) =>
                  setImap((previous) => ({
                    ...(previous ?? defaultImap()),
                    port: Number(event.target.value || '0'),
                  }))
                }
                className="form-input"
              />
            </label>
          </div>
          <p className="text-xs text-stone-500">
            QQ、163/126/yeah.net 邮箱请先开启 IMAP/SMTP，并填写客户端授权码。
          </p>

          <label className="block">
            <div className="mb-2 text-sm font-medium text-stone-800">上传简历</div>
            <input type="file" multiple onChange={handleAddResume} className="form-input py-2" />
          </label>

          {resumes.length > 0 && (
            <div className="space-y-2">
              {resumes.map((resume, index) => (
                <div
                  key={`${resume.name}-${index}`}
                  className="flex items-center justify-between rounded-xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700"
                >
                  <span>{resume.name}</span>
                  <button
                    type="button"
                    onClick={() => setResumes((previous) => previous.filter((_, itemIndex) => itemIndex !== index))}
                    className="text-red-600"
                  >
                    删除
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </details>

      <label className="flex items-center gap-2 text-sm text-stone-700">
        <SelectionToggleButton
          label="设为默认身份"
          selected={isDefault}
          semantics="checkbox"
          size="sm"
          onToggle={() => setIsDefault((current) => !current)}
        />
        设为默认身份
      </label>

      <div className="flex items-center justify-end gap-3 border-t border-stone-100 pt-4">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-xl border border-stone-200 bg-white px-5 py-2.5 text-sm font-medium text-stone-600"
        >
          取消
        </button>
        <button
          type="button"
          onClick={handleSave}
          className="rounded-xl bg-primary px-6 py-2.5 text-sm font-medium text-white"
        >
          {isNew ? '创建身份' : '保存修改'}
        </button>
      </div>
    </div>
  );
};
