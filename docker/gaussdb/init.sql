-- openGauss 测试库初始化脚本（仅在数据卷首次创建时执行）
CREATE DATABASE testdb;

\c testdb

-- 演示表：员工
CREATE TABLE IF NOT EXISTS employees (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    department  VARCHAR(100),
    salary      NUMERIC(12, 2),
    hired_at    TIMESTAMP DEFAULT now()
);

INSERT INTO employees (name, department, salary, hired_at) VALUES
    ('张三', '研发部', 25000.00, now()),
    ('李四', '市场部', 18000.50, now() - interval '30 day'),
    ('王五', '研发部', 22000.00, now() - interval '90 day'),
    ('赵六', '财务部', 16000.00, now() - interval '200 day');

-- 演示表：部门
CREATE TABLE IF NOT EXISTS departments (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    location    VARCHAR(100)
);

INSERT INTO departments (name, location) VALUES
    ('研发部', '北京'),
    ('市场部', '上海'),
    ('财务部', '深圳');
