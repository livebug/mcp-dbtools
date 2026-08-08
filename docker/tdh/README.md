# =============================================================
# TDH (Transwarp Data Hub) 测试环境说明
# =============================================================
#
# 当前工作区已有一个 TDH 容器：tdh-dev（镜像 tdh-standalone:2024.5）。
# 该容器是 Transwarp Manager 平台（端口 8180 控制台 / 10208 agent / 3308
# metastore MySQL），数据栈组件（HDFS / YARN / Inceptor）需要在其管理控制台
# 中部署并启动后才会有 HiveServer2 端口（默认 10000）。
#
# 本 MCP 已把 TDH 接入所需的一切准备好：
#   - 驱动：drivers/inceptor-jdbc.jar（从 tdh-dev 容器 /usr/lib/inceptor/lib/
#     inceptor-jdbc-8.37.3.jar 提取）
#   - 驱动类：org.apache.hive.jdbc.HiveDriver
#   - JDBC URL：jdbc:hive2://<host>:10000/default（部署后端口可能不同）
#   - 方言：TDH 类型使用 SHOW DATABASES / SHOW TABLES / DESCRIBE
#
# 当 Inceptor 的 HiveServer2 起来后：
#   1) 修改 config/datasources.json 中 tdh_inceptor 的 jdbc_url 与账号；
#   2) 用 scripts/java/TdhTest.java 快速验证连接：
#        docker cp scripts/java/TdhTest.java tdh-dev:/tmp/
#        docker exec tdh-dev bash -c "cd /tmp && \
#          /usr/lib/transwarp-manager/common/jdk/bin/javac -cp '/usr/lib/inceptor/lib/*' TdhTest.java && \
#          /usr/lib/transwarp-manager/common/jdk/bin/java -cp '/usr/lib/inceptor/lib/*:/tmp' \
#            TdhTest 'jdbc:hive2://127.0.0.1:10000/default' root '' 'show databases'"
#   3) 重启 MCP 服务即可通过 execute_query 等工具查询 TDH。
#
# 也可通过官方 Docker 镜像部署 Inceptor（需 TDH 商业授权），或直连现有 TDH 集群。

