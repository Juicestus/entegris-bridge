#include "Util.h"
#include "Config.h"
#include "NetInterface.h"
#include "NCSClient.h"

static void PrintStatus(NCSClient& ncs, const char* what, NCSErr status)
{
	printf("  %s -> %s", what, ToString(status));
	if (status == NCSErr::ERR_NETWORK)	printf(" (net %s)", ToString(ncs.NetErr()));
	putchar('\n');
}


#define MAX_COM 8

int main(int argc, char** argv)
{
	Config cfg = Config::FromArgs(argc, argv);
	if (!cfg.test_host[0])
	{
		FATAL("Usage: %s -f <config file>", argv[0]);
		return 1;
	}
	cfg.Print();		


	if (NetInterface::InitNet() != NetErr::OK)
		return 1;
	NCSClient ncs(cfg.test_host, cfg.test_port, &cfg);

	NCSErr status;
	if ((status = ncs.Init()) != NCSErr::OK)
	{
		FATAL("Init failed: %s (net %s)", ToString(status), ToString(ncs.NetErr()));
		NetInterface::CloseNet();
		return 1;
	}

	/*
	* NCS INFO
	*/
	printf("\n * GET_VERSION\n\n");
	char version[256] = { 0 };
	if ((status = ncs.GetVersion(version, sizeof(version))) != NCSErr::OK)
	{
		PrintStatus(ncs, "GET_VERSION", status);		// if this fails the header settings are probably wrong
	}
	else
	{
		printf("  version: %s\n", version);
	}

	printf("\n * GET_NAME\n\n");
	char name[256] = { 0 };
	if ((status = ncs.GetName(name, sizeof(name))) != NCSErr::OK)
	{
		PrintStatus(ncs, "GET_NAME", status);
	}
	else
	{
		printf("  name:    %s\n", name);
	}

	printf("\n * GET_INTERFACES\n\n");
	uint n_iface = 0;
	if ((status = ncs.GetInterfaces(n_iface)) != NCSErr::OK)
	{
		PrintStatus(ncs, "GET_INTERFACES", status);
	}
	
	int n_com = (n_iface >= 1 && n_iface <= MAX_COM) ? (int)n_iface : MAX_COM;
	if ((int)n_iface != n_com)
	{
		printf("  (count unusable, querying %d anyway)\n", n_com);
	}
	


	/*
     * PORT TABLE
	 */
	printf("\n * QUERY_INTERFACE, logical_com1 = %hu\n\n", cfg.logical_com1);
	for (int com = 1; com <= n_com; com++)
	{
		NCSInterfaceInfo info;
		NCSResponse resp;
		if ((status = ncs.QueryInterface(com, &info, &resp)) == NCSErr::OK)
		{
			info.Print(com);
		}
		else if (status == NCSErr::ERR_BAD_RC)
		{
			// 1004 UNKNOWN_SERIAL_PORT here means logical_com1 is wrong, try the other value
			printf("  COM%d (lserial %hu)  rc %hu\n", com, ncs.LSerial(com), (uint16_t)resp.rc);
		}
		else
		{
			PrintStatus(ncs, "QUERY_INTERFACE", status);
		}
	}

	NetInterface::CloseNet();
	printf("Done!\n");
	return 0;
}
