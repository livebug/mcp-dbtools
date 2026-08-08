-- 示例：按日统计报表（演示 ${V_DATE} 参数占位符）
-- 用法：调用 execute_script 时传 params={"V_DATE": "2026-08-08"}
-- 参数来源优先级：params -> 环境变量 -> 缺失报错

SELECT department, count(*) AS emp_count
FROM employees
WHERE hired_at::date <= DATE '${V_DATE}'
GROUP BY department
ORDER BY emp_count DESC;

-- 第二段：查看当前会话信息（只读）
SELECT current_database() AS db, current_user AS usr;
