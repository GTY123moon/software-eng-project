-- =====================================================================
-- 智慧医疗在线问诊与处方流转平台 · 建库建表脚本
-- 数据库：medical_platform（utf8mb4）
-- 说明：14 张业务表，字段口径对齐《实验二 数据库表设计》；
--       标注“扩展字段”的列是业务闭环所需、报告清单未单列，已在报告中补注。
-- =====================================================================

DROP DATABASE IF EXISTS medical_platform;
CREATE DATABASE medical_platform
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_general_ci;

USE medical_platform;

SET FOREIGN_KEY_CHECKS = 0;

-- ---------------------------------------------------------------------
-- 1. 用户表 sys_user（所有账号基础信息）
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS sys_user;
CREATE TABLE sys_user (
    user_id      BIGINT       NOT NULL AUTO_INCREMENT COMMENT '用户ID',
    name         VARCHAR(100) NOT NULL COMMENT '姓名',
    phone        VARCHAR(11)  NOT NULL COMMENT '手机号（唯一登录账号）',
    password     VARCHAR(200) NOT NULL COMMENT '登录密码（加密字符串）',
    role_type    SMALLINT     NOT NULL COMMENT '角色类型：0=管理员，1=患者，2=医生，3=药师',
    status       TINYINT      NOT NULL DEFAULT 1 COMMENT '帐号状态：0=停用，1=启用',
    last_login   DATETIME     NULL COMMENT '扩展字段：最近登录时间',
    PRIMARY KEY (user_id),
    UNIQUE KEY uk_sys_user_phone (phone),
    KEY idx_sys_user_role (role_type),
    CONSTRAINT chk_sys_user_role CHECK (role_type IN (0, 1, 2, 3)),
    CONSTRAINT chk_sys_user_status CHECK (status IN (0, 1))
) ENGINE = InnoDB COMMENT '用户表';

-- ---------------------------------------------------------------------
-- 2. 管理员信息表 admin（与 sys_user 共享主键）
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS admin;
CREATE TABLE admin (
    admin_id    BIGINT       NOT NULL COMMENT '管理员ID，关联 sys_user.user_id',
    account     VARCHAR(50)  NOT NULL COMMENT '扩展字段：登录账号',
    created_at  DATETIME     NOT NULL COMMENT '创建时间',
    PRIMARY KEY (admin_id),
    UNIQUE KEY uk_admin_account (account),
    CONSTRAINT fk_admin_user FOREIGN KEY (admin_id)
        REFERENCES sys_user (user_id)
) ENGINE = InnoDB COMMENT '管理员信息表';

-- ---------------------------------------------------------------------
-- 3. 患者信息表 patient
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS patient;
CREATE TABLE patient (
    patient_id  BIGINT       NOT NULL COMMENT '患者ID，关联 sys_user.user_id',
    id_card_no  VARCHAR(18)  NOT NULL COMMENT '身份证号',
    gender      VARCHAR(20)  NULL COMMENT '性别：男/女/其他',
    age         INT          NULL COMMENT '年龄：0 ≤ age ≤ 200',
    allergy     VARCHAR(500) NULL COMMENT '过敏史',
    address     VARCHAR(200) NULL COMMENT '联系地址',
    medical_history VARCHAR(500) NULL COMMENT '既往病史/慢性病症',
    medical_no  VARCHAR(30)  NOT NULL COMMENT '病历号',
    created_at  DATETIME     NOT NULL COMMENT '注册时间',
    PRIMARY KEY (patient_id),
    UNIQUE KEY uk_patient_idcard (id_card_no),
    UNIQUE KEY uk_patient_medical (medical_no),
    CONSTRAINT fk_patient_user FOREIGN KEY (patient_id)
        REFERENCES sys_user (user_id),
    CONSTRAINT chk_patient_age CHECK (age IS NULL OR age BETWEEN 0 AND 200)
) ENGINE = InnoDB COMMENT '患者信息表';

-- ---------------------------------------------------------------------
-- 4. 医生信息表 doctor
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS doctor;
CREATE TABLE doctor (
    doctor_id      BIGINT       NOT NULL COMMENT '医生ID，关联 sys_user.user_id',
    work_status    SMALLINT     NOT NULL DEFAULT 0 COMMENT '在岗状态：0=离线，1=在线，2=忙碌',
    gender         VARCHAR(20)  NULL COMMENT '性别：男/女/其他',
    department     VARCHAR(100) NOT NULL COMMENT '科室',
    title          VARCHAR(50)  NULL COMMENT '职称：主任医师/副主任医师/主治医师/住院医师',
    skill          VARCHAR(200) NULL COMMENT '擅长简介',
    PRIMARY KEY (doctor_id),
    KEY idx_doctor_dept (department),
    CONSTRAINT fk_doctor_user FOREIGN KEY (doctor_id)
        REFERENCES sys_user (user_id),
    CONSTRAINT chk_doctor_status CHECK (work_status IN (0, 1, 2))
) ENGINE = InnoDB COMMENT '医生信息表';

-- ---------------------------------------------------------------------
-- 5. 药师信息表 pharmacist
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS pharmacist;
CREATE TABLE pharmacist (
    pharmacist_id      BIGINT       NOT NULL COMMENT '药师ID，关联 sys_user.user_id',
    pharmacist_type    VARCHAR(50)  NULL COMMENT '扩展字段：药师类型/职称',
    pharmacist_status  SMALLINT     NOT NULL DEFAULT 0 COMMENT '在岗状态：0=离线，1=在岗',
    gender             VARCHAR(20)  NULL COMMENT '性别：男/女/其他',
    PRIMARY KEY (pharmacist_id),
    CONSTRAINT fk_pharmacist_user FOREIGN KEY (pharmacist_id)
        REFERENCES sys_user (user_id),
    CONSTRAINT chk_pharmacist_status CHECK (pharmacist_status IN (0, 1))
) ENGINE = InnoDB COMMENT '药师信息表';

-- ---------------------------------------------------------------------
-- 6. 药品目录表 drug
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS drug;
CREATE TABLE drug (
    drug_id          BIGINT        NOT NULL AUTO_INCREMENT COMMENT '药品ID',
    drug_code        VARCHAR(40)   NOT NULL COMMENT '药品编码（唯一）',
    name             VARCHAR(100)  NOT NULL COMMENT '药品名称',
    specification    VARCHAR(200)  NOT NULL COMMENT '规格/剂型',
    unit_price       DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '单价：≥0',
    stock_qty        INT           NOT NULL DEFAULT 0 COMMENT '库存数量：≥0',
    is_prescription  SMALLINT      NOT NULL DEFAULT 0 COMMENT '扩展字段：是否处方药：0=非处方药，1=处方药',
    contraindication VARCHAR(200)  NULL COMMENT '禁忌说明：配伍禁忌/慎用提示',
    updated_at       DATETIME      NULL COMMENT '更新时间',
    PRIMARY KEY (drug_id),
    UNIQUE KEY uk_drug_code (drug_code),
    CONSTRAINT chk_drug_price CHECK (unit_price >= 0),
    CONSTRAINT chk_drug_stock CHECK (stock_qty >= 0),
    CONSTRAINT chk_drug_isrx CHECK (is_prescription IN (0, 1))
) ENGINE = InnoDB COMMENT '药品目录表';

-- ---------------------------------------------------------------------
-- 7. 值班排班表 duty_schedule
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS duty_schedule;
CREATE TABLE duty_schedule (
    duty_code  BIGINT       NOT NULL AUTO_INCREMENT COMMENT '值班编号',
    doctor_id  BIGINT       NOT NULL COMMENT '医生ID，关联 doctor',
    shift_type VARCHAR(200) NOT NULL COMMENT '值班类型：白班/夜班',
    start_time DATETIME     NOT NULL COMMENT '班次开始时刻',
    end_time   DATETIME     NOT NULL COMMENT '班次结束时刻',
    status     VARCHAR(200) NOT NULL DEFAULT '正常' COMMENT '状态：正常/调班/停诊',
    updated_at DATETIME     NULL COMMENT '更新时间',
    created_by BIGINT       NOT NULL COMMENT '创建人，关联 admin',
    PRIMARY KEY (duty_code),
    KEY idx_duty_shift (shift_type),
    KEY idx_duty_start (start_time),
    KEY idx_duty_end (end_time),
    CONSTRAINT fk_duty_doctor FOREIGN KEY (doctor_id)
        REFERENCES doctor (doctor_id),
    CONSTRAINT fk_duty_admin FOREIGN KEY (created_by)
        REFERENCES admin (admin_id),
    CONSTRAINT chk_duty_shift CHECK (shift_type IN ('白班', '夜班')),
    CONSTRAINT chk_duty_status CHECK (status IN ('正常', '调班', '停诊'))
) ENGINE = InnoDB COMMENT '值班排班表';

-- ---------------------------------------------------------------------
-- 8. 问诊记录表 consultation
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS consultation;
CREATE TABLE consultation (
    consult_id             BIGINT       NOT NULL AUTO_INCREMENT COMMENT '问诊ID',
    doctor_id             BIGINT       NULL COMMENT '医生ID，关联 doctor',
    patient_id            BIGINT       NOT NULL COMMENT '患者ID，关联 patient',
    duty_code             BIGINT       NULL COMMENT '值班编号，关联 duty_schedule',
    record_content         TEXT         NOT NULL COMMENT '主诉与病史',
    status                 VARCHAR(200) NOT NULL DEFAULT '进行中' COMMENT '问诊状态：进行中/已结束/已转诊/已取消',
    doctor_review_status   VARCHAR(200) NOT NULL DEFAULT '待复核' COMMENT '医生复核状态：待复核/已采纳/已修正/已否决',
    doctor_review_opinion  TEXT         NULL COMMENT '医生复核意见',
    final_diagnosis        TEXT         NULL COMMENT '最终诊断（不能由 AI 直接写入）',
    start_time             DATETIME     NOT NULL COMMENT '问诊发起时间',
    end_time               DATETIME     NULL COMMENT '问诊结束时间',
    PRIMARY KEY (consult_id),
    KEY idx_consult_status (status),
    CONSTRAINT fk_consult_patient FOREIGN KEY (patient_id)
        REFERENCES patient (patient_id),
    CONSTRAINT fk_consult_doctor FOREIGN KEY (doctor_id)
        REFERENCES doctor (doctor_id),
    CONSTRAINT fk_consult_duty FOREIGN KEY (duty_code)
        REFERENCES duty_schedule (duty_code),
    CONSTRAINT chk_consult_status CHECK (status IN ('进行中', '已结束', '已转诊', '已取消')),
    CONSTRAINT chk_consult_review CHECK (doctor_review_status IN ('待复核', '已采纳', '已修正', '已否决'))
) ENGINE = InnoDB COMMENT '问诊记录表';

-- ---------------------------------------------------------------------
-- 9. AI 预问诊评估表 ai_pre_assessment（内部规则引擎产出）
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS ai_pre_assessment;
CREATE TABLE ai_pre_assessment (
    pre_id         BIGINT       NOT NULL AUTO_INCREMENT COMMENT '预评估ID',
    consult_id     BIGINT       NOT NULL COMMENT '问诊ID，关联 consultation',
    risk_level     VARCHAR(200) NOT NULL COMMENT '风险等级：高危/中低危',
    ai_summary     TEXT         NOT NULL COMMENT 'AI 病情摘要（标注仅供参考）',
    matched_keyword VARCHAR(200) NULL COMMENT '命中的高危关键词',
    review_result  VARCHAR(20)  NULL COMMENT '复核结果：已采纳/已修正/已否决',
    review_comment TEXT         NULL COMMENT '复核意见',
    risk_consent   SMALLINT     NOT NULL DEFAULT 0 COMMENT '高危知情同意：0未确认/1已知晓风险并坚持线上就诊',
    created_at     DATETIME     NOT NULL COMMENT '评估生成时间',
    PRIMARY KEY (pre_id),
    KEY idx_pre_risk (risk_level),
    CONSTRAINT fk_pre_consult FOREIGN KEY (consult_id)
        REFERENCES consultation (consult_id),
    CONSTRAINT chk_pre_risk CHECK (risk_level IN ('高危', '中低危')),
    CONSTRAINT chk_pre_review CHECK (review_result IS NULL OR review_result IN ('已采纳', '已修正', '已否决')),
    CONSTRAINT chk_pre_consent CHECK (risk_consent IN (0, 1))
) ENGINE = InnoDB COMMENT 'AI预问诊评估表';

-- ---------------------------------------------------------------------
-- 10. 问诊消息表 consultation_message
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS consultation_message;
CREATE TABLE consultation_message (
    msg_id      BIGINT       NOT NULL AUTO_INCREMENT COMMENT '消息ID',
    consult_id  BIGINT       NOT NULL COMMENT '问诊ID，关联 consultation',
    sender_role VARCHAR(10)  NOT NULL COMMENT '发送方角色：患者/医生',
    sender_id   BIGINT       NOT NULL COMMENT '发送人账号ID，关联 sys_user',
    msg_type    VARCHAR(20)  NOT NULL DEFAULT 'text' COMMENT '消息类型：text/image/video',
    content     TEXT         NULL COMMENT '文本内容',
    media_url   VARCHAR(500) NULL COMMENT '图片/视频地址',
    send_time   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发送时间',
    read_status SMALLINT     NOT NULL DEFAULT 0 COMMENT '已读状态：0=未读，1=已读',
    PRIMARY KEY (msg_id),
    KEY idx_msg_consult (consult_id),
    KEY idx_msg_sendtime (send_time),
    CONSTRAINT fk_msg_consult FOREIGN KEY (consult_id)
        REFERENCES consultation (consult_id),
    CONSTRAINT fk_msg_sender FOREIGN KEY (sender_id)
        REFERENCES sys_user (user_id),
    CONSTRAINT chk_msg_type CHECK (msg_type IN ('text', 'image', 'video')),
    CONSTRAINT chk_msg_read CHECK (read_status IN (0, 1))
) ENGINE = InnoDB COMMENT '问诊消息表';

-- ---------------------------------------------------------------------
-- 11. 转诊日志表 referral_log
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS referral_log;
CREATE TABLE referral_log (
    referral_id      BIGINT       NOT NULL AUTO_INCREMENT COMMENT '转诊ID',
    consult_id       BIGINT       NOT NULL COMMENT '问诊ID，关联 consultation',
    patient_id       BIGINT       NOT NULL COMMENT '患者ID，关联 patient',
    risk_level       VARCHAR(20)  NOT NULL COMMENT '风险等级：高危急症',
    matched_keyword  VARCHAR(200) NULL COMMENT '触发转诊的关键词',
    action           VARCHAR(20)  NOT NULL COMMENT '转诊动作：终止问诊/引导线下急诊',
    operator_id      BIGINT       NULL COMMENT '操作者ID，关联 sys_user',
    patient_choice   VARCHAR(20)  NOT NULL COMMENT '扩展字段：患者选择：确认转诊/坚持线上',
    operator_type    VARCHAR(20)  NULL COMMENT '扩展字段：记录来源：系统/医生/管理员',
    created_at       DATETIME     NOT NULL COMMENT '转诊时间',
    PRIMARY KEY (referral_id),
    KEY idx_referral_risk (risk_level),
    KEY idx_referral_time (created_at),
    CONSTRAINT fk_referral_consult FOREIGN KEY (consult_id)
        REFERENCES consultation (consult_id),
    CONSTRAINT fk_referral_patient FOREIGN KEY (patient_id)
        REFERENCES patient (patient_id),
    CONSTRAINT fk_referral_operator FOREIGN KEY (operator_id)
        REFERENCES sys_user (user_id),
    CONSTRAINT chk_referral_choice CHECK (patient_choice IN ('确认转诊', '坚持线上'))
) ENGINE = InnoDB COMMENT '转诊日志表';

-- ---------------------------------------------------------------------
-- 12. 处方信息表 prescription
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS prescription;
CREATE TABLE prescription (
    presc_id         BIGINT        NOT NULL AUTO_INCREMENT COMMENT '处方ID',
    consult_id       BIGINT        NOT NULL COMMENT '问诊ID，关联 consultation',
    doctor_id        BIGINT        NOT NULL COMMENT '医生ID，关联 doctor',
    patient_id       BIGINT        NOT NULL COMMENT '患者ID，关联 patient',
    status           VARCHAR(200)  NOT NULL DEFAULT '草稿' COMMENT '处方状态：草稿/待审核/已通过/已驳回/待配药/已完成',
    pharmacist_id    BIGINT        NULL COMMENT '审核药师ID，关联 pharmacist',
    audit_conclusion VARCHAR(255)  NULL COMMENT '药师通过/驳回原因',
    audit_time       DATETIME      NULL COMMENT '药师审核时间',
    pickup_method    SMALLINT      NULL COMMENT '取药方式：0=院药房取药，1=社会药店购药',
    pickup_status    SMALLINT      NOT NULL DEFAULT 0 COMMENT '取药状态：0=未取药，1=已取药',
    ai_check_result  VARCHAR(20)   NULL COMMENT '扩展字段：AI药方校验结果',
    ai_check_comment TEXT          NULL COMMENT '扩展字段：AI校验意见（仅供参考）',
    doctor_confirm   SMALLINT      NOT NULL DEFAULT 0 COMMENT '扩展字段：医生人工确认：0=未确认，1=已确认',
    dispensed_at     DATETIME      NULL COMMENT '扩展字段：发药确认时间',
    created_at       DATETIME      NOT NULL COMMENT '开方时间',
    updated_at       DATETIME      NULL COMMENT '更新时间',
    PRIMARY KEY (presc_id),
    KEY idx_presc_consult (consult_id),
    KEY idx_presc_doctor (doctor_id),
    KEY idx_presc_patient (patient_id),
    KEY idx_presc_status (status),
    KEY idx_presc_pharmacist (pharmacist_id),
    CONSTRAINT fk_presc_consult FOREIGN KEY (consult_id)
        REFERENCES consultation (consult_id),
    CONSTRAINT fk_presc_doctor FOREIGN KEY (doctor_id)
        REFERENCES doctor (doctor_id),
    CONSTRAINT fk_presc_patient FOREIGN KEY (patient_id)
        REFERENCES patient (patient_id),
    CONSTRAINT fk_presc_pharmacist FOREIGN KEY (pharmacist_id)
        REFERENCES pharmacist (pharmacist_id),
    CONSTRAINT chk_presc_status CHECK (status IN ('草稿', '待审核', '已通过', '已驳回', '待配药', '已完成')),
    CONSTRAINT chk_presc_aicheck CHECK (ai_check_result IS NULL OR ai_check_result IN ('通过', '风险提示', '超时降级')),
    CONSTRAINT chk_presc_confirm CHECK (doctor_confirm IN (0, 1)),
    CONSTRAINT chk_presc_method CHECK (pickup_method IS NULL OR pickup_method IN (0, 1)),
    CONSTRAINT chk_presc_pickup CHECK (pickup_status IN (0, 1))
) ENGINE = InnoDB COMMENT '处方信息表';

-- ---------------------------------------------------------------------
-- 13. 处方明细表 prescription_item
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS prescription_item;
CREATE TABLE prescription_item (
    presc_id     BIGINT        NOT NULL COMMENT '处方ID，关联 prescription',
    drug_id      BIGINT        NOT NULL COMMENT '药品ID，关联 drug',
    drug_name    VARCHAR(100)  NOT NULL COMMENT '药品名称快照',
    qty          INT           NOT NULL DEFAULT 1 COMMENT '数量：≥1',
    dosage       VARCHAR(200)  NOT NULL COMMENT '用法用量',
    unit_price   DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '单价：≥0',
    subtotal     DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '小计=单价×数量：≥0',
    is_decoction SMALLINT      NOT NULL DEFAULT 0 COMMENT '是否代煎：0=否，1=是',
    decoction_num INT           NULL COMMENT '代煎副数：≥0',
    PRIMARY KEY (presc_id, drug_id),
    KEY idx_item_drug (drug_id),
    CONSTRAINT fk_item_presc FOREIGN KEY (presc_id)
        REFERENCES prescription (presc_id),
    CONSTRAINT fk_item_drug FOREIGN KEY (drug_id)
        REFERENCES drug (drug_id),
    CONSTRAINT chk_item_qty CHECK (qty >= 1),
    CONSTRAINT chk_item_price CHECK (unit_price >= 0 AND subtotal >= 0),
    CONSTRAINT chk_item_decoction CHECK (is_decoction IN (0, 1))
) ENGINE = InnoDB COMMENT '处方明细表';

-- ---------------------------------------------------------------------
-- 14. 操作日志表 operation_log
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS operation_log;
CREATE TABLE operation_log (
    log_id        BIGINT       NOT NULL AUTO_INCREMENT COMMENT '日志ID',
    operator_id   BIGINT       NOT NULL COMMENT '操作人ID，关联 sys_user',
    operator_type VARCHAR(20)  NOT NULL COMMENT '操作人类型：患者/医生/药师/管理员（不含AI）',
    action        VARCHAR(20)  NOT NULL COMMENT '操作动作：登录/复核/开方/审核/排班等',
    target_type   VARCHAR(20)  NULL COMMENT '目标类型：consultation/prescription/duty 等',
    target_id     BIGINT       NULL COMMENT '目标记录ID',
    created_at    DATETIME     NOT NULL COMMENT '操作时间',
    PRIMARY KEY (log_id),
    KEY idx_log_action (action),
    KEY idx_log_target (target_type),
    KEY idx_log_time (created_at),
    CONSTRAINT fk_log_operator FOREIGN KEY (operator_id)
        REFERENCES sys_user (user_id)
) ENGINE = InnoDB COMMENT '操作日志表';

SET FOREIGN_KEY_CHECKS = 1;
