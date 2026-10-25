CREATE INDEX mentor_contact_unanswered ON mentor_contact (last_out_at, mentor_id)
  WHERE status = 'contacted';
