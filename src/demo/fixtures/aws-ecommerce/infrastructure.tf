provider "aws" {
  region = "eu-west-1"
}

variable "db_username" {
  type    = string
  default = "shop_admin"
}

resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
  tags = {
    Name = "shopsphere-vpc"
  }
}

resource "aws_subnet" "public_a" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = "eu-west-1a"
  map_public_ip_on_launch = true
}

resource "aws_subnet" "public_b" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.2.0/24"
  availability_zone       = "eu-west-1b"
  map_public_ip_on_launch = true
}

resource "aws_subnet" "private_a" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.10.0/24"
  availability_zone = "eu-west-1a"
}

resource "aws_subnet" "private_b" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.11.0/24"
  availability_zone = "eu-west-1b"
}

resource "aws_eip" "nat" {
  domain = "vpc"
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public_a.id
}

resource "aws_security_group" "alb" {
  name   = "shopsphere-alb"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "app" {
  name   = "shopsphere-app"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "db" {
  name   = "shopsphere-db"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb" "alb" {
  name               = "shopsphere-alb"
  internal           = false
  load_balancer_type = "application"
  subnets            = [aws_subnet.public_a.id, aws_subnet.public_b.id]
  security_groups    = [aws_security_group.alb.id]
}

resource "aws_lb_target_group" "app" {
  name     = "shopsphere-app"
  port     = 8080
  protocol = "HTTP"
  vpc_id   = aws_vpc.main.id
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.alb.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

resource "aws_iam_role" "app" {
  name               = "shopsphere-app"
  assume_role_policy = <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Principal": { "Service": "ec2.amazonaws.com" }, "Action": "sts:AssumeRole" }
  ]
}
EOF
}

resource "aws_iam_role_policy" "app_inline" {
  name   = "shopsphere-app-inline"
  role   = aws_iam_role.app.id
  policy = <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": "*", "Resource": "*" }
  ]
}
EOF
}

resource "aws_iam_instance_profile" "app" {
  name = "shopsphere-app"
  role = aws_iam_role.app.name
}

resource "aws_launch_template" "app" {
  name_prefix   = "shopsphere-app-"
  image_id      = "ami-0abcdef1234567890"
  instance_type = "m5.large"

  iam_instance_profile {
    name = aws_iam_instance_profile.app.name
  }

  user_data = base64encode("DB_HOST=${aws_db_instance.orders.address} ORDERS_QUEUE=${aws_sqs_queue.orders.url} MEDIA_BUCKET=${aws_s3_bucket.media.id} INVOICE_BUCKET=${aws_s3_bucket.invoices.id}")
}

resource "aws_autoscaling_group" "app" {
  name                = "shopsphere-app"
  min_size            = 1
  max_size            = 4
  desired_capacity    = 1
  vpc_zone_identifier = [aws_subnet.private_a.id]
  target_group_arns   = [aws_lb_target_group.app.arn]

  launch_template {
    id      = aws_launch_template.app.id
    version = "$Latest"
  }
}

resource "aws_db_subnet_group" "main" {
  name       = "shopsphere"
  subnet_ids = [aws_subnet.private_a.id, aws_subnet.private_b.id]
}

resource "aws_db_instance" "orders" {
  identifier              = "shopsphere-orders"
  engine                  = "postgres"
  engine_version          = "15.4"
  instance_class          = "db.r5.4xlarge"
  allocated_storage       = 500
  multi_az                = false
  publicly_accessible     = true
  storage_encrypted       = false
  backup_retention_period = 0
  skip_final_snapshot     = true
  username                = var.db_username
  password                = "Sup3rS3cret!2025"
  db_subnet_group_name    = aws_db_subnet_group.main.name
  vpc_security_group_ids  = [aws_security_group.db.id]

  tags = {
    Data = "customer-pii"
  }
}

resource "aws_s3_bucket" "media" {
  bucket = "shopsphere-media"
  acl    = "public-read"
}

resource "aws_s3_bucket" "invoices" {
  bucket = "shopsphere-invoices"

  tags = {
    Data = "customer-pii"
  }
}

resource "aws_sqs_queue" "orders" {
  name                       = "shopsphere-orders"
  visibility_timeout_seconds = 30
  message_retention_seconds  = 345600
}

resource "aws_cloudwatch_log_group" "app" {
  name = "/shopsphere/app"
}
