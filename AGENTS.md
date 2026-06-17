1.除了python3的脚本，其他都在容器cw-sim2里面运行。
2.容器里面的路径是/root/hpcc_rdma
3.只关注ns-allinone-3.19/hpcc_rdma这个文件夹
4.python2 ./waf configure --build-profile=optimized
5.每次跑之前要把ns-allinone-3.19/hpcc_rdma/mix/output路径下的前一次的文件都删除掉