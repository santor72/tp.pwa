import type { PaymentTransaction } from './api'

export function paymentCommands(transaction: PaymentTransaction) {
  const commands = transaction.pending_commands ?? (
    ['cancel_queued', 'resend_queued', 'select_queued', 'resume_queued'].includes(transaction.current_step)
      ? [transaction.current_step.replace('_queued', '')] : [])
  const canceling = commands.includes('cancel')
  const pending = commands.length > 0
  const message = canceling ? 'Отмена принята. Дождитесь подтверждения — пока не предлагайте клиенту оплачивать.'
    : commands.includes('resend') ? 'Повторная отправка принята в обработку. Это ещё не подтверждение отправки сообщения.'
    : commands.includes('select') ? 'Клиент выбран. Ожидаем продолжения формирования оплаты.'
    : commands.includes('resume') ? 'Повторная обработка принята. Ожидаем продолжения формирования оплаты.'
    : pending ? 'Команда принята. Ожидаем завершения обработки.' : ''
  return { pending, canceling, message }
}
