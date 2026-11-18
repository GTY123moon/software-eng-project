import pymysql

# 让 Django 使用 pymysql 作为 MySQL 驱动（替代默认的 mysqlclient）
pymysql.install_as_MySQLdb()
