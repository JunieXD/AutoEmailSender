export const getEmailSendFailureMessage = (
  status: string | null | undefined,
  failureMessage: string | null | undefined,
) => {
  if (status === "sent" || status === "reply_detected") {
    return null;
  }

  return (
    failureMessage?.trim() || "邮件未成功发送，请检查发信配置后重试。"
  );
};
