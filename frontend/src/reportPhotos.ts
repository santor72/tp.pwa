export const REPORT_PHOTO_ACCEPT = 'image/*,.heic,.heif,.hif,.avif,.tif,.tiff,.bmp,.gif'
export const REPORT_PHOTO_HINT = 'До 5 фотографий, каждая до 25 МиБ, всего до 50 МиБ. Фотографии преобразуются в JPEG.'

// MIME and extensions from phones are unreliable. The server reads the contents.
export function reportPhotoSelectionError(photos: readonly { size: number }[]): string {
  if (photos.length > 5) return 'В одном отчёте можно загрузить до 5 фотографий'
  const oversized = photos.findIndex(photo => photo.size > 25 * 1024 * 1024)
  if (oversized !== -1) return `Фото ${oversized + 1}: размер исходного файла — не более 25 МиБ`
  if (photos.reduce((total, photo) => total + photo.size, 0) > 50 * 1024 * 1024) {
    return 'Общий размер исходных фотографий — не более 50 МиБ'
  }
  return ''
}
