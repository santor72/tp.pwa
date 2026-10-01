import { describe, expect, it } from 'vitest'
import { reportPhotoSelectionError } from '../src/reportPhotos'

const mib = 1024 * 1024

describe('report photo intake limits', () => {
  it('accepts HEIC with unreliable MIME and the limit of 50 MiB', () => {
    const photos = [
      { name: 'phone.heic', type: 'application/octet-stream', size: 25 * mib },
      { name: 'other.tiff', type: '', size: 25 * mib },
    ]
    expect(reportPhotoSelectionError(photos)).toBe('')
  })

  it('identifies a file over 25 MiB', () => {
    expect(reportPhotoSelectionError([{ size: 1 }, { size: 25 * mib + 1 }])).toContain('Фото 2:')
  })

  it('rejects more than five photos and more than 50 MiB in total', () => {
    expect(reportPhotoSelectionError(Array.from({ length: 6 }, () => ({ size: 1 })))).toContain('до 5')
    expect(reportPhotoSelectionError([{ size: 25 * mib }, { size: 25 * mib }, { size: 1 }])).toContain('50 МиБ')
  })
})
