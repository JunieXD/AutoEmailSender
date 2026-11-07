-- When AI looked for todos in this reply, whether or not it found any: turning 回信待办 on again
-- does not look a second time (docs/data-model.md#回信处理队列).
ALTER TABLE message ADD COLUMN todos_extracted_at INTEGER
  CHECK (todos_extracted_at IS NULL OR kind = 'reply');
UPDATE message SET todos_extracted_at =
  (SELECT min(created_at) FROM todo WHERE todo.message_id = message.id)
WHERE id IN (SELECT message_id FROM todo);
