import { describe, expect, it, vi } from 'vitest';
import { filesFromMessages, generatedFileKey, listGeneratedFiles, mergeGeneratedFiles } from './generated-files';
import { apiFetch } from './transport';

vi.mock('./transport', () => ({ apiFetch: vi.fn() }));

const pdf = '/api/v1/export/download/map.pdf';
const delivery = '/api/v1/data-delivery/jobs/job-42/download';
const reply = (content: string) => ({ role: 'assistant', content, timestamp: new Date(2000) });

describe('generated files', () => {
  it('recognizes first-party links, images, references and plain links in assistant prose', () => {
    const files = filesFromMessages([
      { role: 'user', content: `[uploaded](${pdf})` },
      reply(`![Map](${pdf}?exp=1&sig=abc) [Report.pdf][r]\n\n[r]: /api/v1/reports/id/download\n\n${delivery}?member=data.csv`),
    ]);
    expect(files.map(f => [f.name, f.format, f.created_at])).toEqual([
      ['map.pdf', 'pdf', 2], ['Report.pdf', 'pdf', 2], ['data.csv', 'csv', 2],
    ]);
    expect(files[0].download_url).toBe(pdf + '?exp=1&sig=abc');
    expect(generatedFileKey(files[0])).toBe(pdf);
  });

  it('ignores code examples, external sites, non-download routes and unused definitions', () => {
    const content = [
      `[external](https://example.com${pdf})`, `\`[code](${pdf})\``,
      '[other](/api/v1/chat/sessions/private)', `[unused]: ${pdf}`,
      `\n~~~md\n[example](${pdf})\n~~~`,
    ].join('\n\n');
    expect(filesFromMessages([reply(content)])).toEqual([]);
  });

  it('prefers the catalog and newest signed link, but keeps individual delivery members', () => {
    const messages = filesFromMessages([
      reply(`[Map](${pdf}?exp=1&sig=old)`),
      reply(`[Map](${pdf}?exp=2&sig=new) [A](${delivery}?member=a.csv) [B](${delivery}?member=b.csv)`),
    ]);
    expect(mergeGeneratedFiles([], messages).map(f => f.download_url)).toEqual([
      pdf + '?exp=2&sig=new', delivery + '?member=a.csv', delivery + '?member=b.csv',
    ]);
    const expired = { id: 'ref:export/map.pdf', name: 'map.pdf', format: 'pdf', source: 'export' as const, created_at: null, download_url: null };
    expect(mergeGeneratedFiles([expired], messages)).toEqual([expired, messages[1], messages[2]]);
  });

  it('scopes the catalog request and strips unsupported download targets', async () => {
    const signal = new AbortController().signal;
    vi.mocked(apiFetch).mockResolvedValue({items: [
      { id: 'x', source: 'export', download_url: 'https://example.com' + pdf },
    ]});
    expect((await listGeneratedFiles('one/two', { signal, ownerToken: 'owner' }))[0].download_url).toBeNull();
    expect(apiFetch).toHaveBeenCalledWith('/api/v1/chat/sessions/one%2Ftwo/generated-files', {
      cache: 'no-store', signal, ownerToken: 'owner',
    });
  });

  it('groups both addresses of the primary delivery without hiding sidecars', () => {
    const csv = { id: 'delivery:job-42:roads.csv', name: 'roads.csv', format: 'csv', source: 'delivery' as const, created_at: null, download_url: delivery };
    const messages = filesFromMessages([reply(`[Data](${delivery}) [Data](${delivery}?member=roads.csv) [Schema](${delivery}?member=roads.schema.json)`)]);
    expect(mergeGeneratedFiles([csv], messages).map(file => file.name)).toEqual(['roads.csv', 'roads.schema.json']);
    expect(generatedFileKey({ ...csv, download_url: null })).toBe(generatedFileKey(csv));
  });
});
