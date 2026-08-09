import { useState } from 'react';
import { ChatTextArea } from '../atoms/ChatTextArea';
import { SendButton } from '../atoms/SendButton';

interface ChatInputBarProps {
  onSend: (content: string) => void;
}

export const ChatInputBar: React.FC<ChatInputBarProps> = ({ onSend }) => {
  const [content, setContent] = useState('');

  const handleSend = () => {
    if (!content.trim()) return;
    onSend(content.trim());
    setContent('');
  };

  return (
    <div className="sticky bottom-0 bg-white border-t border-stone-100 shadow-[0_-4px_20px_-15px_rgba(0,0,0,0.1)] py-5 px-8">
      <div className="max-w-4xl mx-auto flex items-end gap-4">
        <ChatTextArea value={content} onChange={setContent} placeholder="撰写回复邮件正文…" />
        <div className="mb-6">
          <SendButton onClick={handleSend} disabled={!content.trim()} />
        </div>
      </div>
    </div>
  );
};
