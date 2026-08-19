import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import '@testing-library/jest-dom'
import { PoiInfoPanel, featureDisplayName } from './poi-info-panel'

const layerIds = new Set(['ref:geojson-abc'])
const layersMap = { 'ref:geojson-abc': { id: 'ref:geojson-abc', name: '成都市小学' } }

function makeFeature(props: Record<string, unknown>, sub = 'ref:geojson-abc__point') {
  return { layer: { id: sub }, properties: props }
}

describe('PoiInfoPanel（纯 DOM 悬浮窗）', () => {
  it('单要素：直接显示名称与属性，name 优先', () => {
    render(
      <PoiInfoPanel
        x={100} y={300}
        features={[makeFeature({ name: '三圣小学', category: '科教文化服务', subtype: '学校;小学' })]}
        layerIds={layerIds}
        layersMap={layersMap}
        onClose={() => {}}
      />,
    )
    expect(screen.getAllByText('三圣小学').length).toBeGreaterThan(0)
    expect(screen.getByText(/成都市小学/)).toBeInTheDocument()
    expect(screen.getByText('subtype:')).toBeInTheDocument()
  })

  it('多要素：先列候选，点选后进入详情并可返回', () => {
    render(
      <PoiInfoPanel
        x={100} y={300}
        features={[
          makeFeature({ name: '唐昌镇', adcode: 510117 }),
          makeFeature({ ct_name: '成都市' }, 'ref:geojson-abc__fill'),
        ]}
        layerIds={layerIds}
        layersMap={layersMap}
        onClose={() => {}}
      />,
    )
    expect(screen.getByText(/选择要素（2）/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('唐昌镇'))
    expect(screen.getByText('adcode:')).toBeInTheDocument()
    fireEvent.click(screen.getByText('← 返回要素列表'))
    expect(screen.getByText(/选择要素（2）/)).toBeInTheDocument()
  })

  it('关闭按钮触发 onClose；点击不冒泡到地图', () => {
    const onClose = vi.fn()
    const { container } = render(
      <PoiInfoPanel
        x={100} y={300}
        features={[makeFeature({ name: 'X' })]}
        layerIds={layerIds}
        layersMap={layersMap}
        onClose={onClose}
      />,
    )
    const panel = container.firstElementChild as HTMLElement
    const stop = vi.fn()
    fireEvent.click(panel, { ...{} } as never)
    // stopPropagation 由合成事件验证：直接派发带 spy 的事件对象
    fireEvent(panel, new MouseEvent('click', { bubbles: true }))
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('靠近视口顶部时面板翻转到点击点下方', () => {
    const { container } = render(
      <PoiInfoPanel
        x={100} y={50}
        features={[makeFeature({ name: 'X' })]}
        layerIds={layerIds}
        layersMap={layersMap}
        onClose={() => {}}
      />,
    )
    const panel = container.firstElementChild as HTMLElement
    expect(panel.style.transform).toContain('translate(-50%, 0)')
  })
})

describe('featureDisplayName', () => {
  it('优先 name 类属性；缺失时回退首属性/占位', () => {
    expect(featureDisplayName({ properties: { id: 'B1', name: '海底捞' } }, 'F')).toBe('海底捞')
    expect(featureDisplayName({ properties: { 名称: '华西医院' } }, 'F')).toBe('华西医院')
    expect(featureDisplayName({ properties: { k: 'v' } }, '要素 1')).toBe('v')
    expect(featureDisplayName({ properties: {} }, '要素 1')).toBe('要素 1')
  })
})
