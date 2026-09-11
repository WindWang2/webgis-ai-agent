import { describe, expect, it } from 'vitest';
import { guardFilterText, stripSqlComments } from './guard';

describe('危险语句守卫', () => {
  it('只读过滤表达式放行', () => {
    expect(guardFilterText("type = 'river' AND value > 100").ok).toBe(true);
    expect(guardFilterText('name ILIKE %A%').ok).toBe(true);
    expect(guardFilterText('').ok).toBe(true);
  });

  it('写类动词拦截并给出去重后的动词列表', () => {
    const v = guardFilterText('DELETE FROM t');
    expect(v.ok).toBe(false);
    expect(v.verbs).toEqual(['delete']);
    const multi = guardFilterText('update t set a=1; drop table t');
    expect(multi.ok).toBe(false);
    expect(multi.verbs).toContain('update');
    expect(multi.verbs).toContain('drop');
  });

  it('列名 created_at / update_time 不误伤', () => {
    expect(guardFilterText('created_at > 2020').ok).toBe(true);
    expect(guardFilterText('update_time IS NOT NULL').ok).toBe(true);
    expect(guardFilterText("name = 'deleted'").ok).toBe(true);
  });

  it('注释伪装被剥离后再检测', () => {
    expect(guardFilterText('1=1 /* drop table t */').ok).toBe(true); // 注释内容不是语句
    const sneaky = guardFilterText('1=1 --\ndelete from t');
    expect(sneaky.ok).toBe(false);
  });

  it('多语句拦截：分号后仍有内容', () => {
    expect(guardFilterText('a = 1; b = 2').ok).toBe(false);
    expect(guardFilterText('a = 1;').ok).toBe(true); // 尾分号合法
  });

  it('stripSqlComments 剥三种注释形态', () => {
    expect(stripSqlComments('a -- comment\nb')).toBe('a  \nb');
    expect(stripSqlComments('a /* x */ b')).toBe('a   b');
    expect(stripSqlComments('a # c\nb')).toBe('a  \nb');
  });
});
