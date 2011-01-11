#!/usr/bin/env python

"""@namespace kmeans

Implementation of k-means clustering algorithm (currently works on SVEC data type only).

\par About:

This module implements "k-means clustering" method for a set of data points
accessed via a table or a view. Initial centroids are found according to  
k-means++ algorithm (http://en.wikipedia.org/wiki/K-means%2B%2B). 
Further adjustments are based either on a full data set or on a sample of 
points, such that there are at least 200 points from each cluster.


\par To Do:

- add support for dense arrays (now it's only SVEC)
- make install/usage transparent for Postgresql/Greenplum
- allow the method to start processing a source table w/o a "PointID" column 
    (now it needs both ID and POSITION columns)
- fix the "goodness of fit" test to be scale, rotation and transition invariant
- rewrite the following plpgsql stored procedure into C:
    - func: _kmeans_bestCentroid
    - agg: _kmeans_meanPosition


\par Prerequisites:

Sparse vector data type (svec).


\par Installation:

1) Copy kmeans.py to $GPHOME/lib/python/madlib on all cluster nodes:

- Single node
@verbatim
mkdir $GPHOME/lib/python/madlib
cp kmeans.py $GPHOME/lib/python/madlib@endverbatim

- Multiple node cluster
@verbatim
gpssh -f all_host_file mkdir $GPHOME/lib/python/madlib
gpscp -f all_host_file kmeans.py =:$GPHOME/lib/python/madlib@endverbatim

2) Create database objects:

@verbatim
psql -f setup.sql -d <database>@endverbatim


\par Input Preparation:

1) Prepare the input table/view with the following structure:

@verbatim(
	pid 		INT,  	-- point ID
	position 	SVEC	-- point coordinates
)@endverbatim

Note!:  You can use "create_input.sql" script to create an input table 
        and populate it with random data. See the script for details. 
        By default it will create a table called madlib.kmeans_input
        with 10,000 points (1,000 dimension each).
    

\par Example Execution (in-database):

Call the kmeans_run(...) stored procedure, e.g.:  

@verbatim
SQL> psql -c "select madlib.kmeans_run( 'madlib.kmeans_input', 10, 1, 'run1', 'test');"@endverbatim
    
Parameters:
    - input_table:      table with the source data
    - k:                max number of clusters to consider 
                        (must be smaller than number of input points)
    - goodness:         goodness of fit test flag [0,1]
    - run_id:           execution identifier
    - output_schema:    target schema for output tables (points, centroids)
        

\par Example Execution (outside-database):

Call the provided sample script "kmeans_test.py" with the database connection details:

@verbatim
kmeans_run.py -h <host> -p <port> -U <user> -d <database> @endverbatim
    

\par Sample Output:

@verbatim
INFO: 2010-11-19 11:10:23 : Parameters:
INFO: 2010-11-19 11:10:23 :  * k = 10 (number of centroids)
INFO: 2010-11-19 11:10:23 :  * input_table = madlib.kmeans_input
INFO: 2010-11-19 11:10:23 :  * goodness = 1 (GOF test on)
INFO: 2010-11-19 11:10:23 :  * run_id = testrun
INFO: 2010-11-19 11:10:23 :  * output_schema = madlib
INFO: 2010-11-19 11:10:23 : Seeding 10 centroids...
INFO: 2010-11-19 11:10:24 : Using sample data set for analysis... (9200 out of 10000 points)
INFO: 2010-11-19 11:10:24 : Iteration 1...
INFO: 2010-11-19 11:10:25 : Iteration 2...
INFO: 2010-11-19 11:10:26 : Exit reason: fraction of reassigned nodes is smaller than the limit: 0.001
INFO: 2010-11-19 11:10:26 : Expanding cluster assignment to all points...
INFO: 2010-11-19 11:10:27 : Calculating goodness of fit...

Finished k-means clustering on "madlib"."kmeans_input" table. 
Results: 
    * 10 centroids (goodness of fit = 2.9787570001).
Output:
    * table : "madlib"."kmeans_out_centroids_testrun"
    * table : "madlib"."kmeans_out_points_testrun"
Time elapsed: 0 minutes 4.335644 seconds.@endverbatim

"""

import datetime
import plpy
from math import floor, log, pow

# ----------------------------------------
# K-means global variables
# ----------------------------------------
global output_points, output_centroids

# ----------------------------------------
# Logging
# ----------------------------------------
def info( msg):
    plpy.info( datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + ' : ' + msg);
   
# ----------------------------------------
# Quotes a string to be used as an identifier 
# ----------------------------------------
def quote_ident(val):
    return '"' + val.replace('"', '""') + '"';

# ----------------------------------------
# Quotes a string to be used as a literal 
# ----------------------------------------
def quote_literal(val):
    return "'" + val.replace("'", "''") + "'";
     
# ----------------------------------------
# Function to initialize K centroids
# ----------------------------------------
def __kmeans_init( input_table, k):

	"""	
	Creates the initial set of centroids.
	
	@param input_table Name of relation containing the input data points
	@param k Number of centroids to generate

	@internal
	@endinternal
	
	"""
	
    # Record the time
    start = datetime.datetime.now();
            
    # Find how many points we need to look through to be 99.9% sure that
    # we have seen a point from each cluster 
    numCentroids = floor( - log( 1 - pow( 0.999, 1/float(k))) * k);

    # Create output tables - Points
    plpy.execute( 'DROP TABLE IF EXISTS ' + output_points);
    sql = '''
        CREATE TABLE ''' + output_points + ''' (
            pid BIGINT, 
            position SVEC, 
            cid FLOAT
        ) 
    ''';
    plpy.execute( sql);

    # Create output tables - Centroids
    plpy.execute( 'DROP TABLE IF EXISTS ' + output_centroids);
    sql = '''
        CREATE TABLE ''' + output_centroids + ''' (
            cid INT, 
            position SVEC
        ) 
    ''';
    plpy.execute( sql);

    # Find the 1st centroid
    info( 'Seeding ' + str(k) + ' centroids...');
    sql = '''    
        INSERT INTO ''' + output_centroids + ''' (cid, position) 
        SELECT 1, position 
        FROM ''' + input_table + '''
        ORDER BY random() DESC 
        LIMIT 1
        ''';
    plpy.execute( sql);

    # Find the remaining centroids
    i = 1
    while (i < k):
        i = i + 1;
        sql = '''
            INSERT INTO ''' + output_centroids + ''' (cid, position) 
            SELECT ''' + str(i) + ''', pt.position 
            FROM 
                ''' + input_table + ''' pt,
                ( 
                -- Q1: Find a random point 
                -- (that is furthest away from current Centroids)
                --
                SELECT p.pid, min( l2norm (p.position - c.position)) * (random()^(0.2)) AS distance 
                FROM
                    (SELECT position, pid FROM ''' + input_table + ''' ORDER BY random() LIMIT ''' + str(numCentroids) + ''') AS p -- K random points
                    CROSS JOIN 
                    (SELECT position FROM ''' + output_centroids + ''') AS c -- current centroids
                GROUP BY p.pid ORDER BY distance DESC LIMIT 1
                ) AS pc 
                --
                -- Q1: end
                --
            WHERE pc.pid = pt.pid
        ''';
        plpy.execute( sql);
        # info( "Centroid " + str(i) + " seeded.");

    # Runtime evaluation
    end = datetime.datetime.now();
    minutes, seconds = divmod( (end - start).seconds, 60)
    microsec = (end - start).microseconds
    
    if ('caller' in globals()):
        # If called from KMEANS_RUN
        if ( caller == 'kmeans_run'): return i;
    else:
        # If called directly
        return '''        
Finished seeding %d centroids for %s table/view.
Time elapsed: %d minutes %d.%d seconds.
    ''' % (i, input_table, minutes, seconds, microsec)


# ----------------------------------------
# Function to run the k-means algorithm
# ----------------------------------------
def kmeans_run( input_table, k, goodness, run_id, output_schema):

	"""
	Executes the k-means clustering algorithm.
	
	@param input_table Name of relation containing the input data points
	@param k Number of centroids to generate
	@param goodness Goodness of fit test flag (allowed values: 0,1)
	@param run_id Name/ID of the execution
	@param output_schema Target schema for the output tables.
	
	@internal
	@endinternal
	
	"""
	
    # Record the time
    start = datetime.datetime.now();

    #
    # Adjustable Variables 	
    #
    sampling = 1;               # if set to 1 core sampling is on otherwise algorithm will be executed on the full data set
    sampling_size = 100;        # make the sample size sufficient to have at least 'sampling_size' elements from each cluster with p = .999
    max_sample_size = 10000000; # maximum sample size 
    change_pct_limit = 0.001;   # % of points to change assigment
    max_iterations = 20;        # Maximum number of allowed iterations 

    #
    # Non-Adjustable Variables 	
    #
    p_count = 0;            # number of input points
    c_count = 0;            # number of initial centroids
    c_count_final = 0;      # number of final centroids
    change_pct = [100.0];   # error array
    sample_size = 0;        # sample size - auto generated
    expand = 0;             # set to 1 if the k-means was executed on a sample set
    done = 0;               # loop control variable
    gfit = '';              # goodness of fit description
         
    #   
    # Validate all parameters
    #
    info( 'Parameters:');

    # Validate parameter: k (positive integer)
    if not(k>0):
        plpy.error( "number of centroids must a positive integer (" + str(k) + ")\n");
    else:
        info( ' * k = %s (number of centroids)' % k);
    
    # Validate parameter: input_table
    if ( input_table.find('.') < 0):
        plpy.error( "input table/view is missing a schema prefix (" + input_table + ")\n");
    else:
        input_schemaname, input_tablename = input_table.split('.');
    # Does it exist?
    rv = plpy.execute( "SELECT count(*) as cnt FROM information_schema.columns WHERE "
                       + "table_schema =" + quote_literal( input_schemaname) 
                       + " AND table_name =" + quote_literal( input_tablename));
    if (rv[0]['cnt'] == 0):
        plpy.error( "input table/view does not exists (" + input_table + ")\n");
    # Does it have PID column
    rv = plpy.execute( "SELECT count(*) as cnt FROM information_schema.columns WHERE "
                       + "table_schema = " + quote_literal( input_schemaname) 
                       + " AND table_name = " + quote_literal( input_tablename) 
                       + " AND column_name = 'pid'");
    if (rv[0]['cnt'] == 0):
        plpy.error( "input table/view does not have a PID column (" + input_table + ")\n");
    # Does it have POSITION column
    rv = plpy.execute( "SELECT count(*) as cnt FROM information_schema.columns WHERE "
                       + "table_schema = " + quote_literal( input_schemaname) 
                       + " AND table_name = " + quote_literal( input_tablename) 
                       + " AND column_name = 'position'");
    if (rv[0]['cnt'] == 0):
        plpy.error( "input table/view does not have a POSITION column (" + input_table + ")\n");
    # Input table is OK
    info( ' * input_table = %s' % input_table);
    input_table = quote_ident( input_schemaname) + '.' + quote_ident( input_tablename);
    # But does it have more rows than requested number of centroids
    rv = plpy.execute( "SELECT count(*) as cnt FROM " + input_table);
    p_count = rv[0]['cnt'];
    if (p_count == 0):
        plpy.error( "input table is empty (" + input_table + ")\n");
    elif (p_count <= k):
        plpy.error( "input table has less points (" + str(p_count) + ") "
                    + "than requested number of centroids (" + str(k) + ")\n");
        
    # Validate parameter: goodness
    if (goodness == 1):
        info( ' * goodness = %s (GOF test on)' % str(goodness));
    elif (goodness == 0):
        info( ' * goodness = %s (GOF test off)' % str(goodness));
    else:
        plpy.error( 'incorrect value for goodness: ' + str(goodness) + '\n');
        
    # Validate parameter: run_id
    if (run_id == ''):
        rv = plpy.execute( 'SELECT pg_backend_pid() as pid');
        run_id = str( rv[0]['pid']);
        info( ' * run_id = %s (autogenerated)' % run_id);
    else:
        info( ' * run_id = %s' % run_id);

    # Validate parameter: run_id
    if (output_schema == ''):
        output_schema = 'public'
        info( ' * output_schema = %s' % output_schema);
    else:
        rv = plpy.execute( "SELECT count(*) as cnt FROM pg_namespace WHERE nspname = '%s'" % output_schema);
        if (rv[0]['cnt'] > 0):
            info( ' * output_schema = %s' % output_schema);
        else:
            plpy.error( "output_schema does not exists (" + output_schema + ")\n");


    # Global variables
    global output_points, output_centroids

    # Set Output tables 
    output_points = quote_ident( output_schema) + '.' + quote_ident( 'kmeans_out_points_' + run_id);
    output_centroids = quote_ident( output_schema) + '.' + quote_ident( 'kmeans_out_centroids_' + run_id);

    # Initialize centroids
    global caller;  # set variable to indicate that kmeans_init is called from kmeans_run
    caller = 'kmeans_run';  # as above
    c_count = __kmeans_init( input_table, k);
    if (c_count != k):
        info( 'Requested %d centroids, but %d have been seeded.' % k, c_count);
            
    # Calculate the sample size
    sample_size = min( int( sampling_size * floor( - log( 1 - pow( 0.999, 1/float(k))) * k)), max_sample_size);
	
	# Prepare either the sample or the full data set
    plpy.execute( 'DROP TABLE IF EXISTS TempTable0');	    
    sql = '''
        CREATE TEMP TABLE TempTable0(
            pid BIGINT, 
            position SVEC, 
            cid INTEGER
        )
    ''';
    plpy.execute( sql);
    if (sampling == 1 and sample_size < p_count):
        info( 'Using sample data set for analysis... (' + str(sample_size) + ' out of ' + str(p_count) + ' points)');
        sql = '''
            INSERT INTO TempTable0 
            SELECT pid, position, 0 FROM ''' + input_table + ''' 
            ORDER BY random() 
            LIMIT ''' + str( sample_size);
        expand = 1;
    else:
        info( 'Using full data set for analysis (' + str(p_count) + ' points)');
        sql = '''
            INSERT INTO TempTable0 
            SELECT pid, position, 0 FROM ''' + input_table;
        expand = 0;
    plpy.execute( sql);	    
	
    # Main Loop
    i = 0;
    while (done == 0):	

        # Loop index
        i = i + 1;        
        info( 'Iteration ' + str(i) + '...');
           
        # Create a temporary array of current cetroids
        plpy.execute( 'DROP TABLE IF EXISTS ArrayOfCentroids');	
        sql = '''
            CREATE TEMP TABLE ArrayOfCentroids AS
            SELECT array( 
                SELECT position 
                FROM
                    (SELECT x FROM generate_series( 1, ''' + str(k) + ''') x) x 
                    LEFT OUTER JOIN ''' + output_centroids + ''' c
                    ON x.x = c.cid 
                ORDER BY x
                LIMIT ''' + str(k) + '''
            ) as arr
        ''';
        plpy.execute( sql);	    
		
		# Create new temp table (i)
        plpy.execute( 'DROP TABLE IF EXISTS TempTable' + str(i));
        plpy.execute( 'CREATE TEMP TABLE TempTable' + str(i) + '(like TempTable' + str(i-1) + ')');

		# For each point assign the closest centroid
        sql = '''
            INSERT INTO TempTable''' + str(i) + '''	
            SELECT
                p.pid, 
                p.position, 
                madlib._kmeans_closestID( p.position, arr.arr) as cid 
            FROM 
                TempTable''' + str(i-1) + ''' p CROSS JOIN ArrayOfCentroids arr
        ''';
        plpy.execute( sql);	    

        # Refresh the Centroids table based on current assignments
        plpy.execute( 'TRUNCATE TABLE ' + output_centroids);
        sql = '''
            INSERT INTO ''' + output_centroids + '''
            SELECT t.cid, t.position 
            FROM 
            (
                SELECT 
                    madlib._kmeans_meanPosition( position) AS position
                    , cid AS cid 
                FROM TempTable''' + str(i) + ''' 
                GROUP BY cid
            ) AS t''';
        plpy.execute( sql);	    
        
        # Calculate the number of points that changed the assignment
        sql = '''
            SELECT SUM( ABS( t1.e - t2.e))::float as sum 
            FROM
                (SELECT count( t1.pid) AS e, t1.cid FROM TempTable''' + str(i) + ''' t1 GROUP BY t1.cid) AS t1 
                JOIN 
                (SELECT count( t2.pid) AS e, t2.cid FROM TempTable''' + str(i-1) + ''' t2 GROUP BY t2.cid) AS t2 
                ON t1.cid = t2.cid
        ''';
        rv = plpy.execute( sql);
        
		# Drop old temp table (i-1)
        plpy.execute( 'DROP TABLE TempTable' + str(i-1));
        
        # Add it to the tracking variable
        if (i>1): change_pct.append( rv[0]['sum'] / p_count);

        # Exit conditions:
        if (i>3) and (change_pct[i-4] - change_pct[i-1] < 0): 
            done = 1;
            info( 'Exit reason: fraction of reassigned nodes has stabilized around ' + str(( change_pct[i-4] + change_pct[i-3] + change_pct[i-2] + change_pct[i-1]) / 4.0));            
        elif (change_pct[i-1] < change_pct_limit):
            done = 1;
            info( 'Exit reason: fraction of reassigned nodes is smaller than the limit: ' + str(change_pct_limit));
        elif (i==max_iterations):
            done = 1;
            info( 'Exit reason: reached the maximum number of allowed iterations: ' + str(max_iterations));
        
    # Main Loop - END
            
    # Expand to all points if executed on a sample
    if ( expand == 1):
        info( 'Expanding cluster assignment to all points...');
        sql = '''
            INSERT INTO ''' + output_points + '''	
            SELECT
                p.pid, 
                p.position, 
                madlib._kmeans_closestID( p.position, arr.arr) as cid 
            FROM 
                ''' + input_table + ''' p CROSS JOIN ArrayOfCentroids arr
        ''';
    # Fill the output table
    else:
        info( 'Writing final output table...');
        sql = '''
            INSERT INTO ''' + output_points + '''	
            SELECT * FROM TempTable''' + str(i);    
    
    plpy.execute( sql);	  
            
    # Calculate Goodness of fit
    # This version uses sum(l2norm())/count(*)
    # It would be better to do sum(l2norm(p,c)) / max( avg(points) - point)
    if (goodness==1):
        info( 'Calculating goodness of fit...');
        sql = '''
            SELECT sum( l2norm (p.position - c.position)) / count(*) as gfit
            FROM ''' + output_points + ''' p, ''' + output_centroids + ''' c
            WHERE p.cid = c.cid
        ''';
        rv = plpy.execute( sql);
        gfit = '%s' % rv[0]['gfit'];
    else:
        gfit = 'not computed';

    # Count final number of centroids        
    rv = plpy.execute( 'SELECT count(*) as count FROM ' + output_centroids);
    c_count_final = rv[0]['count'];
        
    # Runtime evaluation
    end = datetime.datetime.now();
    minutes, seconds = divmod( (end - start).seconds, 60)
    microsec = (end - start).microseconds

    return '''
Finished k-means clustering on %s table. 
Results: 
 * %s centroids (goodness of fit = %s).
Output:
 * table : %s
 * table : %s
Time elapsed: %d minutes %d.%d seconds.
    ''' % (input_table, c_count_final, gfit, output_centroids, output_points, minutes, seconds, microsec)