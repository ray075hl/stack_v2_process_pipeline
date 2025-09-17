set -x


# 设置python环境
env_path="/mnt/public/huangliang45/my_conda_env/vllm"
ray_cmd="$env_path/bin/ray"
python_cmd="$env_path/bin/python"

# 启动master node
echo "y" | $ray_cmd start --head --port=6379
launcher_ip=$(cat /etc/hosts | grep launcher | awk '{print $1}' | head -n 1) 
echo "主机ip: $launcher_ip"
# 等待主机启动完成
wait

# 使用命令获取 woker的IP 列表，并将其存储在变量中
ip_list=$(cat /etc/hosts | grep worker | awk '{print $1}')

# 使用 for 循环遍历每个 IP， 启动worker
for ip in $ip_list; do
    # 在这里对每个 IP 执行操作
    echo "Processing IP: $ip"
    # ssh -i /root/.ssh/id_rsa root@$ip -p 2222 "$ray_cmd start --address $launcher_ip:6379" &
    /usr/bin/ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i /sensecore/compute/platform/ssh/ssh_config/id_rsa root@$ip -p 2222 "$ray_cmd start --address $launcher_ip:6379" &
    # 你可以在这里添加其他命令，例如 ping、ssh 等
done

wait

echo "cluster setup finished"
