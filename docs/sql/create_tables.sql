-- =====================================================================
-- 智慧医疗在线问诊与处方流转平台 · 建库建表脚本（按当前本地库实际结构生成）
-- 数据库：medical_platform（utf8mb4 / utf8mb4_general_ci）
-- 说明：14 张业务表；本脚本只建表结构，演示数据由 seed_data.sql、seed_business_extra.sql 灌入。
--       组员部署：先执行本脚本删库重建，再依次灌种子数据，最后 `python manage.py migrate` 建内置框架表。
-- =====================================================================

DROP DATABASE IF EXISTS medical_platform;
CREATE DATABASE medical_platform
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_general_ci;

USE medical_platform;

SET FOREIGN_KEY_CHECKS = 0;


-- ---------------------------------------------------------------------
-- 1. 表 `sys_user`
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS `sys_user`;
CREATE TABLE `sys_user` (
    `user_id` bigint NOT NULL AUTO_INCREMENT COMMENT '用户ID',
    `name` varchar(100) NOT NULL COMMENT '姓名',
    `phone` varchar(11) NOT NULL COMMENT '手机号（唯一登录账号）',
    `password` varchar(200) NOT NULL COMMENT '登录密码（加密字符串）',
    `role_type` smallint NOT NULL COMMENT '角色类型：0=管理员，1=患者，2=医生，3=药师',
    `status` tinyint NOT NULL DEFAULT '1' COMMENT '帐号状态：0=停用，1=启用',
    `last_login` datetime DEFAULT NULL COMMENT '扩展字段：最近登录时间',
    PRIMARY KEY (`user_id`),
    UNIQUE KEY `uk_sys_user_phone` (`phone`),
    KEY `idx_sys_user_role` (`role_type`),
    CONSTRAINT `chk_sys_user_role` CHECK ((`role_type` in (0,1,2,3))),
    CONSTRAINT `chk_sys_user_status` CHECK ((`status` in (0,1)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='用户表';


-- ---------------------------------------------------------------------
-- 2. 表 `admin`
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS `admin`;
CREATE TABLE `admin` (
    `admin_id` bigint NOT NULL COMMENT '管理员ID，关联 sys_user.user_id',
    `account` varchar(50) NOT NULL COMMENT '扩展字段：登录账号',
    `created_at` datetime NOT NULL COMMENT '创建时间',
    PRIMARY KEY (`admin_id`),
    UNIQUE KEY `uk_admin_account` (`account`),
    CONSTRAINT `fk_admin_user` FOREIGN KEY (`admin_id`) REFERENCES `sys_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='管理员信息表';


-- ---------------------------------------------------------------------
-- 3. 表 `patient`
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS `patient`;
CREATE TABLE `patient` (
    `patient_id` bigint NOT NULL COMMENT '患者ID，关联 sys_user.user_id',
    `id_card_no` varchar(18) NOT NULL COMMENT '身份证号',
    `gender` varchar(20) DEFAULT NULL COMMENT '性别：男/女/其他',
    `age` int DEFAULT NULL COMMENT '年龄：0 ≤ age ≤ 200',
    `allergy` varchar(500) DEFAULT NULL COMMENT '过敏史',
    `address` varchar(200) DEFAULT NULL COMMENT '联系地址',
    `medical_history` varchar(500) DEFAULT NULL COMMENT '既往病史/慢性病症',
    `medical_no` varchar(30) NOT NULL COMMENT '病历号',
    `created_at` datetime NOT NULL COMMENT '注册时间',
    PRIMARY KEY (`patient_id`),
    UNIQUE KEY `uk_patient_idcard` (`id_card_no`),
    UNIQUE KEY `uk_patient_medical` (`medical_no`),
    CONSTRAINT `fk_patient_user` FOREIGN KEY (`patient_id`) REFERENCES `sys_user` (`user_id`),
    CONSTRAINT `chk_patient_age` CHECK (((`age` is null) or (`age` between 0 and 200)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='患者信息表';


-- ---------------------------------------------------------------------
-- 4. 表 `doctor`
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS `doctor`;
CREATE TABLE `doctor` (
    `doctor_id` bigint NOT NULL COMMENT '医生ID，关联 sys_user.user_id',
    `work_status` smallint NOT NULL DEFAULT '0' COMMENT '在岗状态：0=离线，1=在线，2=忙碌',
    `gender` varchar(20) DEFAULT NULL COMMENT '性别：男/女/其他',
    `department` varchar(100) NOT NULL COMMENT '科室',
    `title` varchar(50) DEFAULT NULL COMMENT '职称：主任医师/副主任医师/主治医师/住院医师',
    `skill` varchar(200) DEFAULT NULL COMMENT '擅长简介',
    PRIMARY KEY (`doctor_id`),
    KEY `idx_doctor_dept` (`department`),
    CONSTRAINT `fk_doctor_user` FOREIGN KEY (`doctor_id`) REFERENCES `sys_user` (`user_id`),
    CONSTRAINT `chk_doctor_status` CHECK ((`work_status` in (0,1,2)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='医生信息表';


-- ---------------------------------------------------------------------
-- 5. 表 `pharmacist`
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS `pharmacist`;
CREATE TABLE `pharmacist` (
    `pharmacist_id` bigint NOT NULL COMMENT '药师ID，关联 sys_user.user_id',
    `pharmacist_type` varchar(50) DEFAULT NULL COMMENT '扩展字段：药师类型/职称',
    `pharmacist_status` smallint NOT NULL DEFAULT '0' COMMENT '在岗状态：0=离线，1=在岗',
    `gender` varchar(20) DEFAULT NULL COMMENT '性别：男/女/其他',
    PRIMARY KEY (`pharmacist_id`),
    CONSTRAINT `fk_pharmacist_user` FOREIGN KEY (`pharmacist_id`) REFERENCES `sys_user` (`user_id`),
    CONSTRAINT `chk_pharmacist_status` CHECK ((`pharmacist_status` in (0,1)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='药师信息表';


-- ---------------------------------------------------------------------
-- 6. 表 `drug`
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS `drug`;
CREATE TABLE `drug` (
    `drug_id` bigint NOT NULL AUTO_INCREMENT COMMENT '药品ID',
    `drug_code` varchar(40) NOT NULL COMMENT '药品编码（唯一）',
    `name` varchar(100) NOT NULL COMMENT '药品名称',
    `specification` varchar(200) NOT NULL COMMENT '规格/剂型',
    `unit_price` decimal(10,2) NOT NULL DEFAULT '0.00' COMMENT '单价：≥0',
    `stock_qty` int NOT NULL DEFAULT '0' COMMENT '库存数量：≥0',
    `is_prescription` smallint NOT NULL DEFAULT '0' COMMENT '扩展字段：是否处方药：0=非处方药，1=处方药',
    `contraindication` varchar(200) DEFAULT NULL COMMENT '禁忌说明：配伍禁忌/慎用提示',
    `updated_at` datetime DEFAULT NULL COMMENT '更新时间',
    PRIMARY KEY (`drug_id`),
    UNIQUE KEY `uk_drug_code` (`drug_code`),
    CONSTRAINT `chk_drug_isrx` CHECK ((`is_prescription` in (0,1))),
    CONSTRAINT `chk_drug_price` CHECK ((`unit_price` >= 0)),
    CONSTRAINT `chk_drug_stock` CHECK ((`stock_qty` >= 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='药品目录表';


-- ---------------------------------------------------------------------
-- 7. 表 `duty_schedule`
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS `duty_schedule`;
CREATE TABLE `duty_schedule` (
    `duty_code` bigint NOT NULL AUTO_INCREMENT COMMENT '值班编号',
    `doctor_id` bigint NOT NULL COMMENT '医生ID，关联 doctor',
    `shift_type` varchar(200) NOT NULL COMMENT '值班类型：白班/夜班',
    `start_time` datetime NOT NULL COMMENT '班次开始时刻',
    `end_time` datetime NOT NULL COMMENT '班次结束时刻',
    `status` varchar(200) NOT NULL DEFAULT '正常' COMMENT '状态：正常/调班/停诊',
    `weekday` tinyint DEFAULT NULL COMMENT '循环星期：0=周一…6=周日；NULL 为单次手动排班',
    `updated_at` datetime DEFAULT NULL COMMENT '更新时间',
    `created_by` bigint NOT NULL COMMENT '创建人，关联 admin',
    PRIMARY KEY (`duty_code`),
    KEY `idx_duty_shift` (`shift_type`),
    KEY `idx_duty_start` (`start_time`),
    KEY `idx_duty_end` (`end_time`),
    KEY `fk_duty_doctor` (`doctor_id`),
    KEY `fk_duty_admin` (`created_by`),
    CONSTRAINT `fk_duty_admin` FOREIGN KEY (`created_by`) REFERENCES `admin` (`admin_id`),
    CONSTRAINT `fk_duty_doctor` FOREIGN KEY (`doctor_id`) REFERENCES `doctor` (`doctor_id`),
    CONSTRAINT `chk_duty_shift` CHECK ((`shift_type` in ('白班','夜班'))),
    CONSTRAINT `chk_duty_status` CHECK ((`status` in ('正常','调班','停诊')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='值班排班表';


-- ---------------------------------------------------------------------
-- 8. 表 `consultation`
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS `consultation`;
CREATE TABLE `consultation` (
    `consult_id` bigint NOT NULL AUTO_INCREMENT COMMENT '问诊ID',
    `doctor_id` bigint DEFAULT NULL COMMENT '医生ID，关联 doctor',
    `patient_id` bigint NOT NULL COMMENT '患者ID，关联 patient',
    `duty_code` bigint DEFAULT NULL COMMENT '值班编号，关联 duty_schedule',
    `record_content` text NOT NULL COMMENT '主诉与病史',
    `status` varchar(200) NOT NULL DEFAULT '进行中' COMMENT '问诊状态：进行中/已结束/已转诊/已取消',
    `doctor_review_status` varchar(200) NOT NULL DEFAULT '待复核' COMMENT '医生复核状态：待复核/已采纳/已修正/已否决',
    `doctor_review_opinion` text COMMENT '医生复核意见',
    `final_diagnosis` text COMMENT '最终诊断（不能由 AI 直接写入）',
    `advice_type` varchar(20) DEFAULT NULL COMMENT '结束建议：无需用药/需开处方',
    `accepted` smallint NOT NULL DEFAULT 0 COMMENT '接诊标记：0=排队中，1=已接诊（status 仍为进行中）',
    `end_request` smallint NOT NULL DEFAULT 0 COMMENT '结束申请：0=无申请，1=患者申请结束待医生确认',
    `start_time` datetime NOT NULL COMMENT '问诊发起时间',
    `end_time` datetime DEFAULT NULL COMMENT '问诊结束时间',
    PRIMARY KEY (`consult_id`),
    KEY `idx_consult_status` (`status`),
    KEY `fk_consult_patient` (`patient_id`),
    KEY `fk_consult_doctor` (`doctor_id`),
    KEY `fk_consult_duty` (`duty_code`),
    CONSTRAINT `fk_consult_doctor` FOREIGN KEY (`doctor_id`) REFERENCES `doctor` (`doctor_id`),
    CONSTRAINT `fk_consult_duty` FOREIGN KEY (`duty_code`) REFERENCES `duty_schedule` (`duty_code`),
    CONSTRAINT `fk_consult_patient` FOREIGN KEY (`patient_id`) REFERENCES `patient` (`patient_id`),
    CONSTRAINT `chk_consult_review` CHECK ((`doctor_review_status` in ('待复核','已采纳','已修正','已否决'))),
    CONSTRAINT `chk_consult_status` CHECK ((`status` in ('进行中','已结束','已转诊','已取消'))),
    CONSTRAINT `chk_consult_accepted` CHECK ((`accepted` in (0,1))),
    CONSTRAINT `chk_consult_endreq` CHECK ((`end_request` in (0,1)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='问诊记录表';


-- ---------------------------------------------------------------------
-- 9. 表 `ai_pre_assessment`
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS `ai_pre_assessment`;
CREATE TABLE `ai_pre_assessment` (
    `pre_id` bigint NOT NULL AUTO_INCREMENT COMMENT '预评估ID',
    `consult_id` bigint NOT NULL COMMENT '问诊ID，关联 consultation',
    `risk_level` varchar(200) NOT NULL COMMENT '风险等级：高危/中低危',
    `ai_summary` text NOT NULL COMMENT 'AI 病情摘要（标注仅供参考）',
    `matched_keyword` varchar(200) DEFAULT NULL COMMENT '命中的高危关键词',
    `review_result` varchar(20) DEFAULT NULL COMMENT '复核结果：已采纳/已修正/已否决',
    `review_comment` text COMMENT '复核意见',
    `risk_consent` smallint NOT NULL DEFAULT '0' COMMENT '高危知情同意：0未确认/1已知晓风险并坚持线上就诊',
    `created_at` datetime NOT NULL COMMENT '评估生成时间',
    PRIMARY KEY (`pre_id`),
    KEY `idx_pre_risk` (`risk_level`),
    KEY `fk_pre_consult` (`consult_id`),
    CONSTRAINT `fk_pre_consult` FOREIGN KEY (`consult_id`) REFERENCES `consultation` (`consult_id`),
    CONSTRAINT `chk_pre_consent` CHECK ((`risk_consent` in (0,1))),
    CONSTRAINT `chk_pre_review` CHECK (((`review_result` is null) or (`review_result` in ('已采纳','已修正','已否决')))),
    CONSTRAINT `chk_pre_risk` CHECK ((`risk_level` in ('高危','中低危')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='AI预问诊评估表';


-- ---------------------------------------------------------------------
-- 10. 表 `consultation_message`
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS `consultation_message`;
CREATE TABLE `consultation_message` (
    `msg_id` bigint NOT NULL AUTO_INCREMENT COMMENT '消息ID',
    `consult_id` bigint NOT NULL COMMENT '问诊ID，关联 consultation',
    `sender_role` varchar(10) NOT NULL COMMENT '发送方角色：患者/医生',
    `sender_id` bigint NOT NULL COMMENT '发送人账号ID，关联 sys_user',
    `msg_type` varchar(20) NOT NULL DEFAULT 'text' COMMENT '消息类型：text/image/video/pre',
    `content` text COMMENT '文本内容',
    `media_url` varchar(500) DEFAULT NULL COMMENT '图片/视频地址',
    `send_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发送时间',
    `read_status` smallint NOT NULL DEFAULT '0' COMMENT '已读状态：0=未读，1=已读',
    PRIMARY KEY (`msg_id`),
    KEY `idx_msg_consult` (`consult_id`),
    KEY `idx_msg_sendtime` (`send_time`),
    KEY `fk_msg_sender` (`sender_id`),
    CONSTRAINT `fk_msg_consult` FOREIGN KEY (`consult_id`) REFERENCES `consultation` (`consult_id`),
    CONSTRAINT `fk_msg_sender` FOREIGN KEY (`sender_id`) REFERENCES `sys_user` (`user_id`),
    CONSTRAINT `chk_msg_read` CHECK ((`read_status` in (0,1))),
    CONSTRAINT `chk_msg_type` CHECK ((`msg_type` in ('text','image','video','pre')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='问诊消息表';


-- ---------------------------------------------------------------------
-- 11. 表 `referral_log`
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS `referral_log`;
CREATE TABLE `referral_log` (
    `referral_id` bigint NOT NULL AUTO_INCREMENT COMMENT '转诊ID',
    `consult_id` bigint NOT NULL COMMENT '问诊ID，关联 consultation',
    `patient_id` bigint NOT NULL COMMENT '患者ID，关联 patient',
    `risk_level` varchar(20) NOT NULL COMMENT '风险等级：高危急症',
    `matched_keyword` varchar(200) DEFAULT NULL COMMENT '触发转诊的关键词',
    `action` varchar(20) NOT NULL COMMENT '转诊动作：终止问诊/引导线下急诊',
    `operator_id` bigint DEFAULT NULL COMMENT '操作者ID，关联 sys_user',
    `patient_choice` varchar(20) NOT NULL COMMENT '扩展字段：患者选择：确认转诊/坚持线上',
    `operator_type` varchar(20) DEFAULT NULL COMMENT '扩展字段：记录来源：系统/医生/管理员',
    `created_at` datetime NOT NULL COMMENT '转诊时间',
    PRIMARY KEY (`referral_id`),
    KEY `idx_referral_risk` (`risk_level`),
    KEY `idx_referral_time` (`created_at`),
    KEY `fk_referral_consult` (`consult_id`),
    KEY `fk_referral_patient` (`patient_id`),
    KEY `fk_referral_operator` (`operator_id`),
    CONSTRAINT `fk_referral_consult` FOREIGN KEY (`consult_id`) REFERENCES `consultation` (`consult_id`),
    CONSTRAINT `fk_referral_operator` FOREIGN KEY (`operator_id`) REFERENCES `sys_user` (`user_id`),
    CONSTRAINT `fk_referral_patient` FOREIGN KEY (`patient_id`) REFERENCES `patient` (`patient_id`),
    CONSTRAINT `chk_referral_choice` CHECK ((`patient_choice` in ('确认转诊','坚持线上')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='转诊日志表';


-- ---------------------------------------------------------------------
-- 12. 表 `prescription`
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS `prescription`;
CREATE TABLE `prescription` (
    `presc_id` bigint NOT NULL AUTO_INCREMENT COMMENT '处方ID',
    `consult_id` bigint NOT NULL COMMENT '问诊ID，关联 consultation',
    `doctor_id` bigint NOT NULL COMMENT '医生ID，关联 doctor',
    `patient_id` bigint NOT NULL COMMENT '患者ID，关联 patient',
    `status` varchar(200) NOT NULL DEFAULT '草稿' COMMENT '处方状态：草稿/待审核/已通过/已驳回/待配药/已完成',
    `pharmacist_id` bigint DEFAULT NULL COMMENT '审核药师ID，关联 pharmacist',
    `audit_conclusion` varchar(255) DEFAULT NULL COMMENT '药师通过/驳回原因',
    `audit_time` datetime DEFAULT NULL COMMENT '药师审核时间',
    `pickup_method` smallint DEFAULT NULL COMMENT '取药方式：0=院药房取药，1=社会药店购药',
    `pickup_status` smallint NOT NULL DEFAULT '0' COMMENT '取药状态：0=未取药，1=已取药',
    `ai_check_result` varchar(20) DEFAULT NULL COMMENT '扩展字段：AI药方校验结果',
    `ai_check_comment` text COMMENT '扩展字段：AI校验意见（仅供参考）',
    `doctor_confirm` smallint NOT NULL DEFAULT '0' COMMENT '扩展字段：医生人工确认：0=未确认，1=已确认',
    `dispensed_at` datetime DEFAULT NULL COMMENT '扩展字段：发药确认时间',
    `created_at` datetime NOT NULL COMMENT '开方时间',
    `updated_at` datetime DEFAULT NULL COMMENT '更新时间',
    PRIMARY KEY (`presc_id`),
    KEY `idx_presc_consult` (`consult_id`),
    KEY `idx_presc_doctor` (`doctor_id`),
    KEY `idx_presc_patient` (`patient_id`),
    KEY `idx_presc_status` (`status`),
    KEY `idx_presc_pharmacist` (`pharmacist_id`),
    CONSTRAINT `fk_presc_consult` FOREIGN KEY (`consult_id`) REFERENCES `consultation` (`consult_id`),
    CONSTRAINT `fk_presc_doctor` FOREIGN KEY (`doctor_id`) REFERENCES `doctor` (`doctor_id`),
    CONSTRAINT `fk_presc_patient` FOREIGN KEY (`patient_id`) REFERENCES `patient` (`patient_id`),
    CONSTRAINT `fk_presc_pharmacist` FOREIGN KEY (`pharmacist_id`) REFERENCES `pharmacist` (`pharmacist_id`),
    CONSTRAINT `chk_presc_aicheck` CHECK (((`ai_check_result` is null) or (`ai_check_result` in ('通过','风险提示','超时降级')))),
    CONSTRAINT `chk_presc_confirm` CHECK ((`doctor_confirm` in (0,1))),
    CONSTRAINT `chk_presc_method` CHECK (((`pickup_method` is null) or (`pickup_method` in (0,1)))),
    CONSTRAINT `chk_presc_pickup` CHECK ((`pickup_status` in (0,1))),
    CONSTRAINT `chk_presc_status` CHECK ((`status` in ('草稿','待审核','已通过','已驳回','待配药','已完成')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='处方信息表';


-- ---------------------------------------------------------------------
-- 13. 表 `prescription_item`
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS `prescription_item`;
CREATE TABLE `prescription_item` (
    `presc_id` bigint NOT NULL COMMENT '处方ID，关联 prescription',
    `drug_id` bigint NOT NULL COMMENT '药品ID，关联 drug',
    `drug_name` varchar(100) NOT NULL COMMENT '药品名称快照',
    `qty` int NOT NULL DEFAULT '1' COMMENT '数量：≥1',
    `dosage` varchar(200) NOT NULL COMMENT '用法用量',
    `unit_price` decimal(10,2) NOT NULL DEFAULT '0.00' COMMENT '单价：≥0',
    `subtotal` decimal(10,2) NOT NULL DEFAULT '0.00' COMMENT '小计=单价×数量：≥0',
    `is_decoction` smallint NOT NULL DEFAULT '0' COMMENT '是否代煎：0=否，1=是',
    `decoction_num` int DEFAULT NULL COMMENT '代煎副数：≥0',
    PRIMARY KEY (`presc_id`,`drug_id`),
    KEY `idx_item_drug` (`drug_id`),
    CONSTRAINT `fk_item_drug` FOREIGN KEY (`drug_id`) REFERENCES `drug` (`drug_id`),
    CONSTRAINT `fk_item_presc` FOREIGN KEY (`presc_id`) REFERENCES `prescription` (`presc_id`),
    CONSTRAINT `chk_item_decoction` CHECK ((`is_decoction` in (0,1))),
    CONSTRAINT `chk_item_price` CHECK (((`unit_price` >= 0) and (`subtotal` >= 0))),
    CONSTRAINT `chk_item_qty` CHECK ((`qty` >= 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='处方明细表';


-- ---------------------------------------------------------------------
-- 14. 表 `operation_log`
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS `operation_log`;
CREATE TABLE `operation_log` (
    `log_id` bigint NOT NULL AUTO_INCREMENT COMMENT '日志ID',
    `operator_id` bigint NOT NULL COMMENT '操作人ID，关联 sys_user',
    `operator_type` varchar(20) NOT NULL COMMENT '操作人类型：患者/医生/药师/管理员（不含AI）',
    `action` varchar(20) NOT NULL COMMENT '操作动作：登录/复核/开方/审核/排班等',
    `target_type` varchar(20) DEFAULT NULL COMMENT '目标类型：consultation/prescription/duty 等',
    `target_id` bigint DEFAULT NULL COMMENT '目标记录ID',
    `created_at` datetime NOT NULL COMMENT '操作时间',
    PRIMARY KEY (`log_id`),
    KEY `idx_log_action` (`action`),
    KEY `idx_log_target` (`target_type`),
    KEY `idx_log_time` (`created_at`),
    KEY `fk_log_operator` (`operator_id`),
    CONSTRAINT `fk_log_operator` FOREIGN KEY (`operator_id`) REFERENCES `sys_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='操作日志表';


SET FOREIGN_KEY_CHECKS = 1;
